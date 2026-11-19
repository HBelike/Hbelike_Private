"""公开岗位链接的临时可编辑预览；不创建岗位或触发模型分析。"""
from __future__ import annotations

from datetime import UTC, datetime
from html import unescape
from html.parser import HTMLParser
import json
import re
from pathlib import Path

from src.career_assistant.interview_library.public_web import (
    FirecrawlClient, canonicalize_public_url, load_firecrawl_settings,
    validate_public_https_target,
)
from src.career_assistant.job_sources import JobPostingExtractor, _MAX_VISIBLE_TEXT_CHARACTERS


class _JobSchemaParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.inside = False
        self.buffer = []
        self.values = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() == 'script' and dict(attrs).get('type', '').lower() == 'application/ld+json':
            self.inside = True
            self.buffer = []

    def handle_data(self, data):
        if self.inside: self.buffer.append(data)

    def handle_endtag(self, tag):
        if tag.lower() == 'script' and self.inside:
            self.inside = False
            try: self.values.append(json.loads(''.join(self.buffer)))
            except (ValueError, TypeError): pass


def _structured_jobs(value):
    if isinstance(value, list):
        for entry in value: yield from _structured_jobs(entry)
    elif isinstance(value, dict):
        types = value.get('@type', [])
        if isinstance(types, str): types = [types]
        if isinstance(types, list) and 'JobPosting' in types: yield value
        if '@graph' in value: yield from _structured_jobs(value['@graph'])


def _plain_description(value):
    text = re.sub(r'</?(?:p|br|li|div|h[1-6])\b[^>]*>', '\n', str(value or ''), flags=re.I)
    text = unescape(re.sub(r'<[^>]*>', '', text))
    return '\n'.join(line.strip() for line in text.splitlines() if line.strip())


def _check_content(text, *, structured=False):
    if re.search(r'职位已(?:关闭|下架)|岗位已(?:关闭|下架)|招聘已结束|no longer accepting applications|job (?:has been|is) (?:closed|removed)', text, re.I):
        raise ValueError('该岗位已标记下架，请更换链接或粘贴当前有效的职位正文。')
    signals = sum(bool(re.search(pattern, text, re.I)) for pattern in [
        r'职责|负责|responsibilit|what you.ll do',
        r'要求|资格|qualifications?|requirements?|what you.ll (?:need|bring)',
    ])
    if len(text.strip()) < 80 or (not structured and signals < 2):
        raise ValueError('没有读取到完整职位描述，页面可能需要登录或验证。请上传岗位截图，或粘贴职位正文。')


def _job_markdown(value: str) -> str:
    """去掉明确的投递表单和页头图片，不删除岗位职责与要求。"""
    lines = []
    for line in value.splitlines():
        if re.match(r'^#{1,6}\s+(?:Apply for this job|Submit your application|申请此职位|申请该职位)\s*$', line, re.I):
            break
        if line.lstrip().startswith(('![', '[![')):
            continue
        lines.append(line)
    return '\n'.join(lines).strip()


def _names_from_title(title: str | None) -> tuple[str, str]:
    """仅提取招聘页显式标题，不用域名或旧表单猜测名称。"""
    prefix = 'Job Application for '
    if not title or not title.startswith(prefix) or ' at ' not in title:
        return '', ''
    role, company = title[len(prefix):].rsplit(' at ', 1)
    if len(role) > 160 or len(company) > 120:
        return '', ''
    return company.strip(), role.strip()


class JobMaterialImporter:
    def __init__(self, client: FirecrawlClient | None = None):
        self.client = client

    def parse(self, raw_url: str) -> dict:
        # 仅复用校验，不删掉单页招聘站可能用于定位岗位的片段与查询参数。
        url = raw_url.strip()
        canonicalize_public_url(url)
        validate_public_https_target(url)
        company = role = ''
        notices = ['请核对导入正文及岗位是否仍在招，确认前可自由修改。']
        if self.client:
            document = self.client.scrape(url, fresh=True, include_html=True)
            source_url = document.final_url.strip()
            canonicalize_public_url(source_url)
            validate_public_https_target(source_url)
            if int(document.metadata.get('statusCode') or 200) >= 400:
                raise ValueError('职位页面当前无法读取，请上传截图或粘贴职位正文。')
            text = _job_markdown(document.markdown)
            parser = _JobSchemaParser()
            parser.feed(document.raw_html)
            jobs = list(_structured_jobs(parser.values))
            if len(jobs) > 1:
                raise ValueError('该链接包含多个岗位，请打开具体职位详情链接，或上传截图／粘贴正文。')
            if jobs:
                job = jobs[0]
                expiry = job.get('validThrough')
                if expiry:
                    try:
                        deadline = datetime.fromisoformat(str(expiry).replace('Z', '+00:00'))
                        deadline = deadline.replace(tzinfo=UTC) if deadline.tzinfo is None else deadline
                    except ValueError: deadline = None
                    if deadline and deadline < datetime.now(UTC):
                        raise ValueError('该岗位已超过招聘截止时间，请更换链接或粘贴有效职位正文。')
                text = _plain_description(job.get('description'))
                _check_content(text, structured=True)
                organization = job.get('hiringOrganization')
                name = organization.get('name') if isinstance(organization, dict) else None
                company = name[:120] if isinstance(name, str) else ''
                title = job.get('title')
                role = title[:160] if isinstance(title, str) else ''
            else:
                _check_content(text)
                company, role = _names_from_title(document.title)
        else:
            snapshot = JobPostingExtractor().extract(url)
            source_url, text = url, snapshot.visible_text
            # 静态读取器有长度上限，达到上限时不能把可能截断的正文当成完整岗位。
            if len(text) >= _MAX_VISIBLE_TEXT_CHARACTERS:
                raise ValueError('公开页面正文较长，无法确认是否完整。请上传岗位截图，或粘贴完整职位正文。')
            _check_content(text)
            notices.append('本次使用公开页面正文读取；名称未确认时请手动填写。')
        if len(text) > 30_000:
            raise ValueError('职位正文超过导入长度，请选择具体岗位链接，或粘贴所需正文。')
        return {
            'job_text': text.strip(), 'company_name': company, 'role_name': role,
            'source_url': source_url, 'source_kind': 'url',
            'source_label': source_url.split('/')[2],
            'fetched_at': datetime.now(UTC).isoformat(), 'availability': 'unknown',
            'notices': notices,
        }

    def close(self):
        if self.client: self.client.close()


def create_job_material_importer(project_root: Path | None = None) -> JobMaterialImporter:
    # 该预览入口不初始化数据库或模型服务，冷启动时也需独立加载专属配置。
    if project_root is not None:
        environment = project_root / '.env.career-assistant'
        if environment.is_file():
            from dotenv import load_dotenv
            load_dotenv(environment, override=False)
    settings = load_firecrawl_settings()
    return JobMaterialImporter(FirecrawlClient(settings) if settings else None)
