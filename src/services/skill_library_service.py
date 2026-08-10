from __future__ import annotations

import hashlib
import base64
import json
import os
import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from src.config.config_manager import AppConfig
from src.providers.deepseek_provider import DeepSeekMessage, DeepSeekProvider, parse_json_object_from_text


@dataclass(frozen=True)
class SkillSummary:
    """用于 WebUI 列表展示的一条 Skill 摘要。"""

    id: str
    name: str
    description: str
    description_zh: str
    author: str
    homepage_url: str | None
    repository_full_name: str | None
    stars: int | None
    previous_stars: int | None
    star_delta: int | None
    star_growth_rate: float | None
    stars_updated_at: str | None
    source: str
    source_label: str
    path_hint: str
    editable: bool


@dataclass(frozen=True)
class SkillDetail:
    """用于 WebUI 查看和编辑的一条 Skill 详情。"""

    summary: SkillSummary
    markdown: str


@dataclass(frozen=True)
class SkillSearchItem:
    """DS4Pro 或本地回退检索返回的一条候选结果。"""

    skill: SkillSummary
    score: int
    match_reason: str
    markdown: str | None = None


@dataclass(frozen=True)
class SkillSearchResult:
    """Skill 搜索接口的结构化结果。"""

    items: list[SkillSearchItem]
    used_llm: bool
    model: str | None
    fallback_reason: str | None
    search_scope: str
    normalized_query: str


@dataclass(frozen=True)
class SkillSaveResult:
    """Skill 保存接口的结构化结果。"""

    skill: SkillSummary
    markdown: str
    created: bool
    saved_path: Path


class SkillLibraryService:
    """管理本地已安装 Skill 的读取、检索和保存。

    这个服务是 WebUI 的文件层适配器，不触碰业务数据库，也不修改系统/插件 Skill。
    保存时统一写入项目内 `.agents/skills/<skill-name>/SKILL.md`，这样可以安全地
    新增或覆盖项目本地副本，并让列表刷新后立即看到结果。
    """

    _frontmatter_pattern = re.compile(r"^---\s*\n(?P<body>.*?)\n---\s*", re.DOTALL)
    _name_pattern = re.compile(r"^name:\s*(?P<value>.+?)\s*$", re.MULTILINE)
    _description_pattern = re.compile(r"^description:\s*(?P<value>.+?)\s*$", re.MULTILINE)
    _frontmatter_field_pattern_template = r"^{field}:\s*(?P<value>.+?)\s*$"
    _skill_name_pattern = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]{1,79}$")

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.project_skill_root = (self.config.project_root / ".agents" / "skills").resolve()
        self.star_cache_path = (self.config.project_root / "data" / "skill_star_cache.json").resolve()
        self.star_cache_ttl = timedelta(days=7)

    def list_skills(self) -> list[SkillSummary]:
        """扫描当前可读的 Skill 目录，并返回去重后的摘要列表。"""

        summaries: dict[str, SkillSummary] = {}
        for skill_path, source, source_label in self._iter_skill_files():
            try:
                markdown = self._read_text(skill_path)
            except OSError:
                continue

            name, description = self._parse_frontmatter(markdown, fallback_name=skill_path.parent.name)
            author = self._frontmatter_field(markdown, ["author", "authors", "maintainer"]) or source_label
            homepage_url = self._frontmatter_field(markdown, ["homepage", "url", "repo", "repository", "link"])
            repository_full_name = self._repository_full_name_from_metadata(
                homepage_url=homepage_url,
                path_hint=self._path_hint(skill_path),
                markdown=markdown,
            )
            star_snapshot = self._repository_star_snapshot(repository_full_name)
            skill_id = self._skill_id(skill_path)
            summary = SkillSummary(
                id=skill_id,
                name=name,
                description=description,
                description_zh=self._local_chinese_description(name=name, description=description),
                author=author,
                homepage_url=homepage_url,
                repository_full_name=repository_full_name,
                stars=star_snapshot["stars"],
                previous_stars=star_snapshot["previous_stars"],
                star_delta=star_snapshot["star_delta"],
                star_growth_rate=star_snapshot["star_growth_rate"],
                stars_updated_at=star_snapshot["stars_updated_at"],
                source=source,
                source_label=source_label,
                path_hint=self._path_hint(skill_path),
                editable=self._is_project_skill(skill_path),
            )

            existing = summaries.get(name)
            if existing is None or self._source_priority(summary.source) < self._source_priority(existing.source):
                summaries[name] = summary

        return sorted(
            summaries.values(),
            key=lambda item: (
                0 if item.name == "find-skills" else 1,
                self._source_priority(item.source),
                item.name.lower(),
            ),
        )

    def get_skill(self, skill_id: str) -> SkillDetail:
        """根据前端传入的稳定 id 读取 Skill.md。"""

        skill_path = self._resolve_skill_path(skill_id)
        markdown = self._read_text(skill_path)
        name, description = self._parse_frontmatter(markdown, fallback_name=skill_path.parent.name)
        source_label = self._source_label_for_path(skill_path)
        homepage_url = self._frontmatter_field(markdown, ["homepage", "url", "repo", "repository", "link"])
        path_hint = self._path_hint(skill_path)
        repository_full_name = self._repository_full_name_from_metadata(
            homepage_url=homepage_url,
            path_hint=path_hint,
            markdown=markdown,
        )
        star_snapshot = self._repository_star_snapshot(repository_full_name)
        summary = SkillSummary(
            id=self._skill_id(skill_path),
            name=name,
            description=description,
            description_zh=self._local_chinese_description(name=name, description=description),
            author=self._frontmatter_field(markdown, ["author", "authors", "maintainer"]) or source_label,
            homepage_url=homepage_url,
            repository_full_name=repository_full_name,
            stars=star_snapshot["stars"],
            previous_stars=star_snapshot["previous_stars"],
            star_delta=star_snapshot["star_delta"],
            star_growth_rate=star_snapshot["star_growth_rate"],
            stars_updated_at=star_snapshot["stars_updated_at"],
            source=self._source_for_path(skill_path),
            source_label=source_label,
            path_hint=path_hint,
            editable=self._is_project_skill(skill_path),
        )
        return SkillDetail(summary=summary, markdown=markdown)

    def search_skills(self, query: str) -> SkillSearchResult:
        """用 DS4Pro 对 Skill 列表做意图排序；失败时自动回退本地关键词检索。"""

        normalized_query = query.strip()
        skills = self.list_skills()
        if not normalized_query:
            return SkillSearchResult(
                items=[
                    SkillSearchItem(skill=skill, score=90, match_reason="未输入关键词，展示已安装 Skill。")
                    for skill in skills[:20]
                ],
                used_llm=False,
                model=None,
                fallback_reason=None,
                search_scope="local_installed",
                normalized_query="",
            )

        try:
            open_result = self._search_open_skills(query=normalized_query)
        except Exception as exc:
            return SkillSearchResult(
                items=self._search_locally(query=normalized_query, skills=skills),
                used_llm=False,
                model=None,
                fallback_reason=f"GitHub Skill 搜索暂不可用，已临时展示本地已安装结果：{exc.__class__.__name__}",
                search_scope="fallback_local_installed",
                normalized_query=normalized_query,
            )

        if open_result.items:
            return open_result

        return SkillSearchResult(
            items=self._search_locally(query=normalized_query, skills=skills),
            used_llm=False,
            model=open_result.model,
            fallback_reason="GitHub 暂未返回可用 Skill，已临时展示本地已安装结果。",
            search_scope="fallback_local_installed",
            normalized_query=open_result.normalized_query,
        )

    def save_skill(
        self,
        name: str,
        markdown: str,
        source_repository_full_name: str | None = None,
        source_homepage_url: str | None = None,
        source_author: str | None = None,
    ) -> SkillSaveResult:
        """把编辑后的 Skill.md 保存到项目本地 Skill 目录，并返回新的摘要。"""

        normalized_name = self._normalize_skill_name(name=name, markdown=markdown)
        normalized_markdown = self._ensure_frontmatter_name(markdown=markdown, name=normalized_name)
        normalized_markdown = self._ensure_saved_skill_metadata(
            markdown=normalized_markdown,
            source_repository_full_name=source_repository_full_name,
            source_homepage_url=source_homepage_url,
            source_author=source_author,
        )
        destination_dir = (self.project_skill_root / normalized_name).resolve()
        self._assert_inside_project_skill_root(destination_dir)
        destination_path = destination_dir / "SKILL.md"
        created = not destination_path.exists()

        destination_dir.mkdir(parents=True, exist_ok=True)
        destination_path.write_text(normalized_markdown, encoding="utf-8")

        saved_detail = self.get_skill(self._skill_id(destination_path))
        return SkillSaveResult(
            skill=saved_detail.summary,
            markdown=saved_detail.markdown,
            created=created,
            saved_path=destination_path,
        )

    def _ensure_saved_skill_metadata(
        self,
        markdown: str,
        source_repository_full_name: str | None,
        source_homepage_url: str | None,
        source_author: str | None,
    ) -> str:
        """保存远端 Skill 时，把仓库信息写入 frontmatter，方便后续刷新 Star。"""

        metadata: dict[str, str] = {}
        if source_repository_full_name and not self._frontmatter_field(markdown, ["repository_full_name", "repository"]):
            metadata["repository_full_name"] = source_repository_full_name
        if source_homepage_url and not self._frontmatter_field(markdown, ["homepage", "url", "link"]):
            metadata["homepage"] = source_homepage_url
        if source_author and not self._frontmatter_field(markdown, ["author", "authors", "maintainer"]):
            metadata["author"] = source_author
        if not metadata:
            return markdown

        match = self._frontmatter_pattern.match(markdown.strip())
        if match is None:
            return markdown

        frontmatter = match.group("body").rstrip()
        additions = "\n".join(f"{key}: {value}" for key, value in metadata.items())
        body_start = match.end()
        return f"---\n{frontmatter}\n{additions}\n---\n{markdown[body_start:]}"

    def _search_open_skills(self, query: str) -> SkillSearchResult:
        """按 find-skills 思路搜索 GitHub 上的开放 Skill。"""

        normalized_query, model = self._normalize_open_skill_query(query=query)
        markdown_files = self._search_github_skill_files(query=normalized_query)
        items: list[SkillSearchItem] = []
        seen_names: set[str] = set()

        for index, item in enumerate(markdown_files, start=1):
            markdown = str(item["markdown"])
            path = str(item["path"])
            repository = item["repository"]
            owner = str(repository.get("owner", {}).get("login", "GitHub"))
            repo_name = str(repository.get("full_name", "unknown/repository"))
            fallback_name = self._skill_name_from_github_path(path=path, repo_name=repo_name)
            name, description = self._parse_frontmatter(markdown, fallback_name=fallback_name)
            normalized_name_key = name.lower()
            if normalized_name_key in seen_names:
                continue
            seen_names.add(normalized_name_key)

            html_url = str(item.get("html_url", "")).strip() or None
            repository_full_name = repo_name if "/" in repo_name else None
            star_snapshot = self._repository_star_snapshot(repository_full_name)
            summary = SkillSummary(
                id=f"github-{str(item.get('sha', hashlib.sha256(str(item).encode('utf-8')).hexdigest()))[:16]}",
                name=name,
                description=description,
                description_zh=self._local_chinese_description(name=name, description=description),
                author=owner,
                homepage_url=html_url,
                repository_full_name=repository_full_name,
                stars=star_snapshot["stars"],
                previous_stars=star_snapshot["previous_stars"],
                star_delta=star_snapshot["star_delta"],
                star_growth_rate=star_snapshot["star_growth_rate"],
                stars_updated_at=star_snapshot["stars_updated_at"],
                source="github",
                source_label="GitHub",
                path_hint=f"{repo_name}/{path}",
                editable=False,
            )
            score = max(60, 98 - index * 4)
            reason = f"来自 GitHub 仓库 {repo_name}，匹配开放 Skill 文件 {path}。"
            items.append(SkillSearchItem(skill=summary, score=score, match_reason=reason, markdown=markdown))
            if len(items) >= 8:
                break

        items = self._localize_open_skill_items(query=query, items=items)
        return SkillSearchResult(
            items=items,
            used_llm=model is not None,
            model=model,
            fallback_reason=None,
            search_scope="github_open_skills",
            normalized_query=normalized_query,
        )

    def _normalize_open_skill_query(self, query: str) -> tuple[str, str | None]:
        """用 DS4Pro 把中文搜索意图改写为 GitHub Code Search 更容易命中的英文关键词。"""

        provider = DeepSeekProvider(config=self.config, run_name="skills.query_normalize")
        skills = self.list_skills()
        find_skill = next((skill for skill in skills if skill.name == "find-skills"), None)
        find_skill_markdown = ""
        if find_skill is not None:
            find_skill_markdown = self.get_skill(find_skill.id).markdown[:1800]

        system_prompt = (
            "你是一个 GitHub Skill 搜索关键词改写助手。只返回 JSON，不要解释。"
        )
        user_prompt = f"""
用户关键词：
{query}

find-skills 的本地说明片段：
{find_skill_markdown}

请返回 JSON 对象，格式必须是：
{{
  "github_query": "3 到 8 个英文关键词，空格分隔，不要包含 filename:SKILL.md"
}}

要求：
- 面向 GitHub Code Search；
- 保留技术名词，例如 React、video、prompt、WeChat、agent；
- 如果用户输入中文，请翻译为常见英文技术关键词；
- 不要添加路径限定语法，代码会自动追加 filename:SKILL.md。
"""
        response = provider.chat(
            [
                DeepSeekMessage(role="system", content=system_prompt),
                DeepSeekMessage(role="user", content=user_prompt.strip()),
            ]
        )
        parsed = parse_json_object_from_text(response.content)
        normalized_query = str(parsed.get("github_query", "")).strip()
        if not normalized_query:
            return query, response.model
        return normalized_query[:180], response.model

    def _search_github_skill_files(self, query: str) -> list[dict[str, Any]]:
        """调用 GitHub Code Search 查找 SKILL.md，并拉取文件内容。"""

        search_terms = query.strip() or "agent skills"
        search_query = f"{search_terms} filename:SKILL.md"
        response = requests.get(
            "https://api.github.com/search/code",
            headers=self._github_headers(),
            params={
                "q": search_query,
                "per_page": 12,
            },
            timeout=25,
        )
        if not response.ok:
            raise RuntimeError(f"GitHub Code Search 失败：status={response.status_code}")

        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("GitHub Code Search 响应不是对象")

        raw_items = payload.get("items", [])
        if not isinstance(raw_items, list):
            return []

        results: list[dict[str, Any]] = []
        for raw_item in raw_items:
            if not isinstance(raw_item, dict):
                continue
            try:
                markdown = self._fetch_github_file_text(raw_item)
            except Exception:
                continue
            if not markdown.strip():
                continue
            enriched = dict(raw_item)
            enriched["markdown"] = markdown
            results.append(enriched)
            if len(results) >= 8:
                break
        return results

    def _fetch_github_file_text(self, code_search_item: dict[str, Any]) -> str:
        """读取 GitHub Code Search 返回的单个文件内容。"""

        api_url = str(code_search_item.get("url", "")).strip()
        if not api_url.startswith("https://api.github.com/"):
            raise RuntimeError("GitHub 文件 API 地址无效")

        response = requests.get(api_url, headers=self._github_headers(), timeout=20)
        if not response.ok:
            raise RuntimeError(f"GitHub 文件读取失败：status={response.status_code}")

        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("GitHub 文件响应不是对象")

        content = str(payload.get("content", ""))
        encoding = str(payload.get("encoding", "")).lower()
        if encoding == "base64":
            return base64.b64decode(content).decode("utf-8", errors="replace")

        download_url = str(payload.get("download_url", "")).strip()
        if download_url.startswith("https://"):
            raw_response = requests.get(download_url, timeout=20)
            if raw_response.ok:
                return raw_response.text
        return content

    def _github_headers(self) -> dict[str, str]:
        """构造 GitHub API 请求头。"""

        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self.config.github_api_version,
        }
        token = os.getenv(self.config.github_token_env, "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _skill_name_from_github_path(self, path: str, repo_name: str) -> str:
        """从 GitHub 路径推断 Skill 名称。"""

        parts = [part for part in path.replace("\\", "/").split("/") if part]
        if len(parts) >= 2 and parts[-1].lower() == "skill.md":
            return parts[-2]
        return repo_name.split("/", 1)[-1]

    def _localize_open_skill_items(self, query: str, items: list[SkillSearchItem]) -> list[SkillSearchItem]:
        """把 GitHub 搜索结果中面向用户展示的描述统一改为中文。"""

        if not items:
            return items

        try:
            provider = DeepSeekProvider(config=self.config, run_name="skills.result_rank")
            candidates = [
                {
                    "skill_id": item.skill.id,
                    "name": item.skill.name,
                    "description": item.skill.description[:500],
                    "author": item.skill.author,
                    "repository": item.skill.repository_full_name,
                    "path": item.skill.path_hint,
                }
                for item in items
            ]
            system_prompt = (
                "你是技术 Skill 搜索结果的中文信息整理助手。只返回 JSON。"
                "除人名、仓库名、Skill 名、API、框架名、模型名等关键技术名词外，其余面向用户的展示文字必须使用中文。"
            )
            user_prompt = f"""
用户搜索词：
{query}

GitHub Skill 候选：
{candidates}

请返回 JSON：
{{
  "items": [
    {{
      "skill_id": "候选 skill_id",
      "description_zh": "一句中文简介，保留必要英文技术名词",
      "match_reason": "一句中文匹配原因，保留作者名、仓库名、关键技术名词"
    }}
  ]
}}

要求：
- 不要翻译人名、仓库名、Skill 名；
- 不要把 API、React、GitHub、WeChat、prompt、agent、Skill 等技术词强行翻译；
- 不要编造安装量或 Star 数。
"""
            response = provider.chat(
                [
                    DeepSeekMessage(role="system", content=system_prompt),
                    DeepSeekMessage(role="user", content=user_prompt.strip()),
                ]
            )
            parsed = parse_json_object_from_text(response.content)
            raw_items = parsed.get("items", [])
            if not isinstance(raw_items, list):
                return items
            localized_by_id: dict[str, dict[str, str]] = {}
            for raw_item in raw_items:
                if not isinstance(raw_item, dict):
                    continue
                skill_id = str(raw_item.get("skill_id", "")).strip()
                if not skill_id:
                    continue
                localized_by_id[skill_id] = {
                    "description_zh": str(raw_item.get("description_zh", "")).strip(),
                    "match_reason": str(raw_item.get("match_reason", "")).strip(),
                }
        except Exception:
            return items

        localized_items: list[SkillSearchItem] = []
        for item in items:
            localized = localized_by_id.get(item.skill.id, {})
            description_zh = localized.get("description_zh") or item.skill.description_zh
            match_reason = localized.get("match_reason") or item.match_reason
            localized_items.append(
                SkillSearchItem(
                    skill=replace(item.skill, description_zh=description_zh),
                    score=item.score,
                    match_reason=match_reason,
                    markdown=item.markdown,
                )
            )
        return localized_items

    def _repository_full_name_from_metadata(
        self,
        homepage_url: str | None,
        path_hint: str,
        markdown: str,
    ) -> str | None:
        """从 frontmatter、链接或路径中识别 GitHub 仓库 full_name。"""

        candidates = [
            self._frontmatter_field(markdown, ["repository_full_name", "github_repository", "repository"]),
            homepage_url,
        ]
        if "github.com/" in path_hint:
            candidates.append(path_hint)
        for candidate in candidates:
            repository = self._extract_github_repository_full_name(str(candidate or ""))
            if repository:
                return repository
        return None

    def _extract_github_repository_full_name(self, value: str) -> str | None:
        """从 GitHub URL 或 owner/repo 文本中提取仓库名。"""

        normalized = value.strip().replace("\\", "/")
        if not normalized:
            return None

        github_match = re.search(r"github\.com/([^/\s]+)/([^/\s#?]+)", normalized)
        if github_match:
            owner = github_match.group(1).strip()
            repo = github_match.group(2).strip().removesuffix(".git")
            if owner and repo:
                return f"{owner}/{repo}"

        owner_repo_match = re.search(r"(^|[^a-zA-Z0-9_.-])([a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+)(/|$)", normalized)
        if owner_repo_match:
            return owner_repo_match.group(2).removesuffix(".git")
        return None

    def _repository_star_snapshot(self, repository_full_name: str | None) -> dict[str, Any]:
        """读取或按七天周期刷新 GitHub 仓库 Star 快照。"""

        empty_snapshot = {
            "stars": None,
            "previous_stars": None,
            "star_delta": None,
            "star_growth_rate": None,
            "stars_updated_at": None,
        }
        if not repository_full_name:
            return empty_snapshot

        cache = self._load_star_cache()
        repositories = cache.setdefault("repositories", {})
        raw_record = repositories.get(repository_full_name)
        record = raw_record if isinstance(raw_record, dict) else {}
        now = datetime.now(UTC)

        if not self._is_star_record_stale(record, now=now):
            return self._star_payload_from_record(record)

        try:
            latest_stars = self._fetch_repository_stars(repository_full_name)
        except Exception:
            if record:
                return self._star_payload_from_record(record)
            return empty_snapshot

        previous_stars = record.get("stars") if isinstance(record.get("stars"), int) else None
        previous_updated_at = str(record.get("updated_at", "")) if record.get("updated_at") else None
        updated_record = {
            "stars": latest_stars,
            "previous_stars": previous_stars,
            "updated_at": now.isoformat(),
            "previous_updated_at": previous_updated_at,
        }
        repositories[repository_full_name] = updated_record
        self._save_star_cache(cache)
        return self._star_payload_from_record(updated_record)

    def _is_star_record_stale(self, record: dict[str, Any], now: datetime) -> bool:
        """判断 Star 缓存是否超过七天。"""

        if not record or not isinstance(record.get("stars"), int):
            return True
        updated_at = str(record.get("updated_at", "")).strip()
        if not updated_at:
            return True
        try:
            parsed = datetime.fromisoformat(updated_at)
        except ValueError:
            return True
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return now - parsed >= self.star_cache_ttl

    def _fetch_repository_stars(self, repository_full_name: str) -> int:
        """调用 GitHub 仓库接口读取真实 stargazers_count。"""

        response = requests.get(
            f"https://api.github.com/repos/{repository_full_name}",
            headers=self._github_headers(),
            timeout=20,
        )
        if not response.ok:
            raise RuntimeError(f"GitHub 仓库读取失败：status={response.status_code}")
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("GitHub 仓库响应不是对象")
        return int(payload.get("stargazers_count", 0))

    def _star_payload_from_record(self, record: dict[str, Any]) -> dict[str, Any]:
        """把缓存记录转换为前端展示字段。"""

        stars = record.get("stars") if isinstance(record.get("stars"), int) else None
        previous_stars = record.get("previous_stars") if isinstance(record.get("previous_stars"), int) else None
        star_delta = None
        star_growth_rate = None
        if stars is not None and previous_stars is not None:
            star_delta = stars - previous_stars
            if previous_stars > 0:
                star_growth_rate = star_delta / previous_stars
        return {
            "stars": stars,
            "previous_stars": previous_stars,
            "star_delta": star_delta,
            "star_growth_rate": star_growth_rate,
            "stars_updated_at": str(record.get("updated_at", "")) or None,
        }

    def _load_star_cache(self) -> dict[str, Any]:
        """读取本地 Star 缓存。"""

        if not self.star_cache_path.exists():
            return {"repositories": {}}
        try:
            payload = json.loads(self.star_cache_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"repositories": {}}
        if not isinstance(payload, dict):
            return {"repositories": {}}
        repositories = payload.get("repositories")
        if not isinstance(repositories, dict):
            payload["repositories"] = {}
        return payload

    def _save_star_cache(self, cache: dict[str, Any]) -> None:
        """保存本地 Star 缓存。"""

        self.star_cache_path.parent.mkdir(parents=True, exist_ok=True)
        self.star_cache_path.write_text(
            json.dumps(cache, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _local_chinese_description(self, name: str, description: str) -> str:
        """为本地列表提供中文展示兜底，保留 Skill 名和技术词。"""

        if self._contains_cjk(description):
            return description
        if description.strip():
            return f"{name}：用于扩展 Agent 工作流，覆盖该 Skill 描述中的专门能力。"
        return f"{name}：本地已安装 Skill。"

    def _contains_cjk(self, value: str) -> bool:
        """判断文本是否包含中文字符。"""

        return bool(re.search(r"[\u4e00-\u9fff]", value))

    def _search_locally(self, query: str, skills: list[SkillSummary]) -> list[SkillSearchItem]:
        """模型不可用时使用朴素关键词匹配，保证 UI 可用。"""

        tokens = [token for token in re.split(r"[\s,，。;；/\\]+", query.lower()) if token]
        ranked: list[SkillSearchItem] = []
        for skill in skills:
            haystack = f"{skill.name} {skill.description} {skill.source_label}".lower()
            matched = [token for token in tokens if token in haystack]
            if skill.name == "find-skills" and tokens:
                matched.append("find-skills")
            if not matched:
                continue
            score = min(95, 50 + len(set(matched)) * 12)
            ranked.append(
                SkillSearchItem(
                    skill=skill,
                    score=score,
                    match_reason=f"本地命中关键词：{'、'.join(sorted(set(matched))[:5])}",
                )
            )

        if not ranked:
            return [
                SkillSearchItem(
                    skill=skill,
                    score=60,
                    match_reason="未找到精确命中，展示本地已安装 Skill 供人工判断。",
                )
                for skill in skills[:8]
            ]

        return sorted(ranked, key=lambda item: (-item.score, item.skill.name.lower()))[:8]

    def _iter_skill_files(self) -> list[tuple[Path, str, str]]:
        """返回所有候选 SKILL.md 文件及其来源标签。"""

        home = Path.home()
        scan_roots: list[tuple[Path, str, str, bool]] = [
            (self.project_skill_root, "project", "项目本地", False),
            (self.config.project_root / ".codex" / "skills", "project", "项目本地", False),
            (home / ".agents" / "skills", "user", "用户已安装", False),
            (home / ".codex" / "skills", "user", "用户已安装", False),
            (home / ".codex" / "plugins" / "cache", "plugin", "插件内置", True),
        ]

        results: list[tuple[Path, str, str]] = []
        seen_paths: set[Path] = set()
        for root, source, source_label, recursive in scan_roots:
            resolved_root = root.resolve()
            if not resolved_root.exists() or not resolved_root.is_dir():
                continue
            candidates = resolved_root.rglob("SKILL.md") if recursive else resolved_root.glob("**/SKILL.md")
            for candidate in candidates:
                try:
                    resolved_candidate = candidate.resolve()
                except OSError:
                    continue
                if resolved_candidate in seen_paths:
                    continue
                seen_paths.add(resolved_candidate)
                results.append((resolved_candidate, source, source_label))

        return results

    def _resolve_skill_path(self, skill_id: str) -> Path:
        """重新扫描本地 Skill，并根据 id 找到对应 SKILL.md。"""

        normalized_id = skill_id.strip()
        for skill_path, _source, _source_label in self._iter_skill_files():
            if self._skill_id(skill_path) == normalized_id:
                return skill_path
        raise FileNotFoundError(f"Skill 不存在或已移动：{skill_id}")

    def _parse_frontmatter(self, markdown: str, fallback_name: str) -> tuple[str, str]:
        """从 SKILL.md frontmatter 中提取 name 和 description。"""

        match = self._frontmatter_pattern.match(markdown.strip())
        frontmatter = match.group("body") if match else ""

        raw_name = ""
        raw_description = ""
        name_match = self._name_pattern.search(frontmatter)
        description_match = self._description_pattern.search(frontmatter)
        if name_match:
            raw_name = name_match.group("value").strip().strip("\"'")
        if description_match:
            raw_description = description_match.group("value").strip().strip("\"'")

        name = raw_name or fallback_name
        description = raw_description or self._first_meaningful_line(markdown)
        return name, description

    def _frontmatter_field(self, markdown: str, field_names: list[str]) -> str | None:
        """读取 SKILL.md frontmatter 中的可选字段。"""

        match = self._frontmatter_pattern.match(markdown.strip())
        if match is None:
            return None

        frontmatter = match.group("body")
        for field_name in field_names:
            pattern = re.compile(
                self._frontmatter_field_pattern_template.format(field=re.escape(field_name)),
                re.MULTILINE,
            )
            field_match = pattern.search(frontmatter)
            if field_match is None:
                continue
            value = field_match.group("value").strip().strip("\"'")
            if value:
                return value
        return None

    def _first_meaningful_line(self, markdown: str) -> str:
        """当 description 缺失时，从正文提取一句短描述。"""

        for line in markdown.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("---") or stripped.startswith("name:"):
                continue
            if stripped.startswith("description:"):
                continue
            return stripped.lstrip("#").strip()[:160]
        return "暂无描述"

    def _normalize_skill_name(self, name: str, markdown: str) -> str:
        """校验并规范化保存时使用的 Skill 名称。"""

        parsed_name, _description = self._parse_frontmatter(markdown, fallback_name="")
        candidate = (name.strip() or parsed_name.strip()).strip().strip("\"'")
        candidate = candidate.replace(" ", "-")
        if not self._skill_name_pattern.match(candidate):
            raise ValueError("Skill 名称只能包含字母、数字、短横线和下划线，长度 2-80，且必须以字母或数字开头。")
        return candidate

    def _ensure_frontmatter_name(self, markdown: str, name: str) -> str:
        """确保保存后的 SKILL.md frontmatter 中包含最新 name。"""

        normalized_markdown = markdown.strip() + "\n"
        match = self._frontmatter_pattern.match(normalized_markdown)
        if match is None:
            return f"---\nname: {name}\ndescription: 本地保存的 Skill。\n---\n\n{normalized_markdown}"

        frontmatter = match.group("body")
        if self._name_pattern.search(frontmatter):
            updated_frontmatter = self._name_pattern.sub(f"name: {name}", frontmatter, count=1)
        else:
            updated_frontmatter = f"name: {name}\n{frontmatter}".strip()

        body_start = match.end()
        return f"---\n{updated_frontmatter}\n---\n{normalized_markdown[body_start:]}"

    def _skill_id(self, skill_path: Path) -> str:
        """根据绝对路径生成稳定但不暴露路径的 id。"""

        normalized_path = str(skill_path.resolve()).replace("\\", "/").lower()
        return hashlib.sha256(normalized_path.encode("utf-8")).hexdigest()[:16]

    def _path_hint(self, skill_path: Path) -> str:
        """生成适合 UI 展示的路径提示，不暴露过长绝对路径。"""

        resolved_path = skill_path.resolve()
        roots = [
            self.config.project_root.resolve(),
            (Path.home() / ".agents").resolve(),
            (Path.home() / ".codex").resolve(),
        ]
        for root in roots:
            try:
                return str(resolved_path.relative_to(root)).replace("\\", "/")
            except ValueError:
                continue
        return resolved_path.name

    def _source_for_path(self, skill_path: Path) -> str:
        """根据路径判断 Skill 来源。"""

        resolved_path = skill_path.resolve()
        if self._is_project_skill(resolved_path):
            return "project"
        plugin_root = (Path.home() / ".codex" / "plugins" / "cache").resolve()
        try:
            resolved_path.relative_to(plugin_root)
            return "plugin"
        except ValueError:
            return "user"

    def _source_label_for_path(self, skill_path: Path) -> str:
        """根据路径生成来源中文标签。"""

        source = self._source_for_path(skill_path)
        if source == "project":
            return "项目本地"
        if source == "plugin":
            return "插件内置"
        return "用户已安装"

    def _is_project_skill(self, skill_path: Path) -> bool:
        """判断 Skill 是否位于项目本地可保存目录。"""

        resolved_path = skill_path.resolve()
        project_roots = [
            self.project_skill_root,
            (self.config.project_root / ".codex" / "skills").resolve(),
        ]
        for root in project_roots:
            try:
                resolved_path.relative_to(root)
                return True
            except ValueError:
                continue
        return False

    def _assert_inside_project_skill_root(self, destination_dir: Path) -> None:
        """保存前做路径边界检查，避免写出项目本地 Skill 根目录。"""

        try:
            destination_dir.relative_to(self.project_skill_root)
        except ValueError as exc:
            raise ValueError("保存路径不在项目本地 Skill 目录内。") from exc

    def _source_priority(self, source: str) -> int:
        """同名 Skill 去重时，项目本地副本优先，其次用户安装，最后插件内置。"""

        return {
            "project": 0,
            "user": 1,
            "plugin": 2,
        }.get(source, 9)

    def _read_text(self, skill_path: Path) -> str:
        """读取 SKILL.md，兼容 UTF-8 BOM。"""

        return skill_path.read_text(encoding="utf-8-sig")

    def _clamp_score(self, raw_score: Any) -> int:
        """把模型给出的分数收敛到 0-100。"""

        try:
            score = int(raw_score)
        except (TypeError, ValueError):
            score = 80
        return max(0, min(score, 100))
