"""公开岗位导入预览的内容与网络边界，不写业务资料。"""
import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.career_assistant.interview_library.public_web import FirecrawlClient, FirecrawlSettings
from src.career_assistant.job_material_import import JobMaterialImporter, create_job_material_importer
from src.career_assistant.job_sources import JobPostingSnapshot
from src.career_assistant.web import router as web_router

DESCRIPTION = '负责业务系统开发、接口设计与服务维护。任职要求：熟悉 Python 和 SQL，能够独立定位线上问题，参与代码评审和团队协作。' * 3


def make_importer(monkeypatch, *, markdown=DESCRIPTION, html='', status=200, title='Python 工程师'):
    monkeypatch.setattr('src.career_assistant.job_material_import.validate_public_https_target', lambda value: None)
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={'success': True, 'data': {
            'markdown': markdown, 'rawHtml': html,
            'metadata': {'title': title, 'url': 'https://jobs.example.org/engineer', 'statusCode': status},
        }})
    client = FirecrawlClient(FirecrawlSettings('test-key'), transport=httpx.MockTransport(handler))
    return JobMaterialImporter(client), requests


def job_html(**extra):
    data = {'@type': 'JobPosting', 'title': 'Python 工程师', 'hiringOrganization': {'name': '示例公司'}, 'description': DESCRIPTION, **extra}
    return '<script type="application/ld+json">'+json.dumps(data)+'</script>'


def test_structured_job_uses_confirmable_fields_and_fresh_scrape(monkeypatch):
    importer, requests = make_importer(monkeypatch, html=job_html())
    try:
        result = importer.parse('https://jobs.example.org/engineer')
        assert result['company_name'] == '示例公司'
        assert result['role_name'] == 'Python 工程师'
        assert result['job_text'] == DESCRIPTION
        assert result['availability'] == 'unknown'
        assert requests[0]['maxAge'] == 0
        assert requests[0]['storeInCache'] is False
        assert 'rawHtml' in requests[0]['formats']
        assert 'html' not in result
    finally:
        importer.close()


@pytest.mark.parametrize('body', ['登录后查看职位详情，请打开 App', 'Access denied. Please verify you are human.', '# Careers\n查看全部职位列表', ''])
def test_login_verification_lists_and_empty_content_are_not_import_success(monkeypatch, body):
    importer, _ = make_importer(monkeypatch, markdown=body)
    with pytest.raises(ValueError, match='截图|正文'):
        importer.parse('https://jobs.example.org/engineer')
    importer.close()


def test_expired_structured_job_is_rejected(monkeypatch):
    importer, _ = make_importer(monkeypatch, html=job_html(validThrough=(datetime.now(UTC)-timedelta(days=1)).isoformat()))
    with pytest.raises(ValueError, match='截止|下架'):
        importer.parse('https://jobs.example.org/engineer')
    importer.close()


def test_names_are_left_empty_without_evidence(monkeypatch):
    importer, _ = make_importer(monkeypatch)
    result = importer.parse('https://jobs.example.org/engineer')
    assert result['company_name'] == ''
    assert result['role_name'] == ''
    assert result['job_text'] == DESCRIPTION
    importer.close()


def test_explicit_ats_title_and_application_form_boundary(monkeypatch):
    markdown = '[![Company Logo](https://img.example/logo.png)](https://example.org)\n\n'+DESCRIPTION+'\n\n# Apply for this job\n私人申请表单字段'
    importer, _ = make_importer(monkeypatch, markdown=markdown, title='Job Application for Python Engineer at Example Company')
    result = importer.parse('https://jobs.example.org/job')
    assert result['company_name'] == 'Example Company'
    assert result['role_name'] == 'Python Engineer'
    assert result['job_text'] == DESCRIPTION
    assert '申请表单字段' not in result['job_text']
    importer.close()


def test_job_identity_fragment_is_sent_to_rendering_provider(monkeypatch):
    importer, requests = make_importer(monkeypatch)
    importer.parse('https://jobs.example.org/#/position/123?department=software')
    assert requests[0]['url'].endswith('#/position/123?department=software')
    importer.close()


def test_malformed_schema_falls_back_to_readable_markdown(monkeypatch):
    importer, _ = make_importer(monkeypatch, html='<script type="application/ld+json">{"@type": 1}</script>')
    assert importer.parse('https://jobs.example.org/job')['job_text'] == DESCRIPTION
    importer.close()


def test_static_fallback_without_firecrawl_keeps_unknown_names(monkeypatch):
    monkeypatch.setattr('src.career_assistant.job_material_import.validate_public_https_target', lambda value: None)
    monkeypatch.setattr('src.career_assistant.job_material_import.JobPostingExtractor.extract', lambda self, url: JobPostingSnapshot('jobs.example.org', '岗位', DESCRIPTION, datetime.now(UTC)))
    result = JobMaterialImporter().parse('https://jobs.example.org/job')
    assert result['job_text'] == DESCRIPTION
    assert result['company_name'] == ''


def test_static_fallback_does_not_import_possibly_truncated_body(monkeypatch):
    from src.career_assistant.job_sources import _MAX_VISIBLE_TEXT_CHARACTERS
    monkeypatch.setattr('src.career_assistant.job_material_import.validate_public_https_target', lambda value: None)
    monkeypatch.setattr('src.career_assistant.job_material_import.JobPostingExtractor.extract', lambda self, url: JobPostingSnapshot('jobs.example.org', '岗位', DESCRIPTION.ljust(_MAX_VISIBLE_TEXT_CHARACTERS, '字'), datetime.now(UTC)))
    with pytest.raises(ValueError, match='无法确认是否完整'):
        JobMaterialImporter().parse('https://jobs.example.org/job')


def test_upstream_forbidden_page_is_not_imported_even_with_readable_body(monkeypatch):
    importer, _ = make_importer(monkeypatch, status=403)
    with pytest.raises(ValueError, match='截图|正文'):
        importer.parse('https://jobs.example.org/job')
    importer.close()


def test_provider_failure_returns_actionable_error_and_closes(monkeypatch):
    from src.career_assistant.interview_library.public_web import FirecrawlRequestError
    class Importer:
        closed = False
        def parse(self, url): raise FirecrawlRequestError('auth', 'private-provider-detail', retryable=False)
        def close(self): self.closed = True
    importer = Importer()
    monkeypatch.setattr(web_router, 'create_job_material_importer', lambda root=None: importer)
    app = FastAPI()
    app.include_router(web_router.router)
    with TestClient(app) as client:
        response = client.post('/api/career/target-role-profiles/parse-url', json={'url': 'https://jobs.example.org/job'})
    assert response.status_code == 503
    assert '上传岗位截图' in response.json()['detail']
    assert 'private-provider-detail' not in response.text
    assert importer.closed


@pytest.mark.parametrize('url', ['https://127.0.0.1/a', 'https://localhost/a', 'file:///private', 'https://user:secret@example.org/a'])
def test_invalid_input_never_calls_provider(url):
    called = []
    client = FirecrawlClient(FirecrawlSettings('test-key'), transport=httpx.MockTransport(lambda request: called.append(request)))
    importer = JobMaterialImporter(client)
    with pytest.raises(ValueError): importer.parse(url)
    assert called == []
    importer.close()


def test_preview_route_does_not_require_or_write_repository(monkeypatch):
    class Importer:
        closed = False
        def parse(self, url): return {'job_text': DESCRIPTION, 'source_url': url, 'company_name': '', 'role_name': ''}
        def close(self): self.closed = True
    importer = Importer()
    monkeypatch.setattr(web_router, 'create_job_material_importer', lambda root=None: importer)
    app = FastAPI()
    app.include_router(web_router.router)
    with TestClient(app) as client:
        response = client.post('/api/career/target-role-profiles/parse-url', json={'url': 'https://jobs.example.org/engineer'})
    assert response.status_code == 200
    assert response.json()['job_text'] == DESCRIPTION
    assert importer.closed


def test_cold_start_reads_project_config_without_database(tmp_path, monkeypatch):
    monkeypatch.delenv('FIRECRAWL_API_KEY', raising=False)
    (tmp_path / '.env.career-assistant').write_text('FIRECRAWL_API_KEY=cold-start-test-key\n', encoding='utf-8')
    importer = create_job_material_importer(tmp_path)
    assert importer.client is not None
    assert not (tmp_path / 'data').exists()
    importer.close()
