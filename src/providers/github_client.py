from __future__ import annotations

import os
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import requests

from src.config.config_manager import AppConfig


class GitHubApiError(RuntimeError):
    """GitHub API 调用失败时抛出的业务异常。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(f"GitHub API 请求失败：status_code={status_code} message={message}")
        self.status_code = status_code
        self.message = message


@dataclass(frozen=True)
class GitHubSearchRequest:
    """GitHub Search API 请求描述。"""

    method: str
    url: str
    params: dict[str, Any]
    headers: dict[str, str]
    timeout_seconds: float
    token_configured: bool

    def safe_headers(self) -> dict[str, str]:
        """返回可写入日志或数据库的脱敏请求头。"""
        safe: dict[str, str] = {}
        for key, value in self.headers.items():
            if key.lower() == "authorization":
                safe[key] = "Bearer ***"
            else:
                safe[key] = value
        return safe


@dataclass(frozen=True)
class GitHubRepositoryItem:
    """从 GitHub Search API 解析出的仓库基础信息。"""

    github_id: int
    owner: str
    name: str
    full_name: str
    html_url: str
    description: str | None
    language: str | None
    stars: int
    forks: int
    open_issues: int
    default_branch: str | None
    pushed_at: str | None
    github_updated_at: str | None
    github_created_at: str | None
    is_archived: bool
    is_fork: bool


@dataclass(frozen=True)
class GitHubSearchResponse:
    """GitHub Search API 返回的候选仓库集合。"""

    request: GitHubSearchRequest
    token_configured: bool
    token_accepted: bool
    auth_fallback_used: bool
    total_count: int
    incomplete_results: bool
    items: list[GitHubRepositoryItem]
    rate_limit_remaining: str | None
    rate_limit_reset: str | None


class GitHubClient:
    """负责 GitHub API 的请求构造、调用和响应解析。"""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.timezone = ZoneInfo(config.timezone_name)

    def build_weekly_candidate_search_request(
        self,
        now: datetime | None = None,
    ) -> GitHubSearchRequest:
        """构造每周候选项目搜索请求。"""
        current = now if now is not None else datetime.now(self.timezone)
        if current.tzinfo is None:
            current = current.replace(tzinfo=self.timezone)
        else:
            current = current.astimezone(self.timezone)

        query = self._build_query(current)
        token = self._read_token()
        headers = self._build_headers(token)

        per_page = min(self.config.github_per_page, self.config.github_candidate_limit, 100)
        params: dict[str, Any] = {
            "q": query,
            "sort": self.config.github_sort,
            "order": self.config.github_order,
            "per_page": per_page,
            "page": 1,
        }

        return GitHubSearchRequest(
            method="GET",
            url=self._build_search_url(),
            params=params,
            headers=headers,
            timeout_seconds=self.config.github_timeout_seconds,
            token_configured=bool(token),
        )

    def search_weekly_candidates(self) -> GitHubSearchResponse:
        """调用 GitHub Search API，拉取候选仓库。"""
        request = self.build_weekly_candidate_search_request()
        current_request = request
        auth_fallback_used = False
        fetched_items: list[GitHubRepositoryItem] = []
        total_count = 0
        incomplete_results = False
        rate_limit_remaining: str | None = None
        rate_limit_reset: str | None = None

        page = 1
        while len(fetched_items) < self.config.github_candidate_limit:
            params = dict(current_request.params)
            params["page"] = page
            remaining_limit = self.config.github_candidate_limit - len(fetched_items)
            params["per_page"] = min(int(params["per_page"]), remaining_limit)

            response = requests.get(
                current_request.url,
                headers=current_request.headers,
                params=params,
                timeout=current_request.timeout_seconds,
            )
            rate_limit_remaining = response.headers.get("X-RateLimit-Remaining")
            rate_limit_reset = response.headers.get("X-RateLimit-Reset")

            if response.status_code == 401 and current_request.token_configured and not auth_fallback_used:
                current_request = replace(
                    request,
                    headers=self._build_headers(token=None),
                    token_configured=False,
                )
                auth_fallback_used = True
                continue

            payload = self._parse_json_response(response)
            total_count = int(payload.get("total_count", 0))
            incomplete_results = bool(payload.get("incomplete_results", False))
            page_items = payload.get("items", [])
            if not isinstance(page_items, list):
                raise GitHubApiError(response.status_code, "GitHub Search 响应 items 字段不是列表")

            for item in page_items:
                fetched_items.append(self._parse_repository_item(item))
                if len(fetched_items) >= self.config.github_candidate_limit:
                    break

            if len(page_items) < int(params["per_page"]):
                break

            page += 1

        return GitHubSearchResponse(
            request=current_request,
            token_configured=request.token_configured,
            token_accepted=request.token_configured and not auth_fallback_used,
            auth_fallback_used=auth_fallback_used,
            total_count=total_count,
            incomplete_results=incomplete_results,
            items=fetched_items,
            rate_limit_remaining=rate_limit_remaining,
            rate_limit_reset=rate_limit_reset,
        )

    def _build_query(self, current: datetime) -> str:
        """根据配置生成 GitHub Search 查询语句。"""
        query_parts = [
            self.config.github_search_query.strip(),
            f"stars:>={self.config.github_min_stars}",
        ]

        if self.config.github_pushed_within_days > 0:
            since_date = (current - timedelta(days=self.config.github_pushed_within_days)).date()
            query_parts.append(f"pushed:>={since_date.isoformat()}")

        return " ".join(part for part in query_parts if part)

    def _build_search_url(self) -> str:
        """拼出 GitHub Search API URL。"""
        base_url = self.config.github_api_base_url.rstrip("/")
        endpoint = self.config.github_search_endpoint.lstrip("/")
        return f"{base_url}/{endpoint}"

    def _build_headers(self, token: str | None) -> dict[str, str]:
        """构造 GitHub API 请求头，避免把密钥写入日志。"""
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": self.config.github_api_version,
            "User-Agent": self.config.app_name,
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _read_token(self) -> str | None:
        """从环境变量读取 GitHub Token。"""
        token = os.getenv(self.config.github_token_env, "").strip()
        if not token:
            return None
        return token

    def _parse_json_response(self, response: requests.Response) -> dict[str, Any]:
        """解析 GitHub JSON 响应，并把非 2xx 响应转换为 GitHubApiError。"""
        try:
            payload = response.json()
        except ValueError as exc:
            raise GitHubApiError(response.status_code, "GitHub API 返回了非 JSON 响应") from exc

        if not response.ok:
            message = "未知错误"
            if isinstance(payload, dict):
                message = str(payload.get("message", message))
            raise GitHubApiError(response.status_code, message)

        if not isinstance(payload, dict):
            raise GitHubApiError(response.status_code, "GitHub API JSON 响应不是对象")

        return payload

    def _parse_repository_item(self, item: Any) -> GitHubRepositoryItem:
        """把 GitHub 仓库 JSON 转成内部结构。"""
        if not isinstance(item, dict):
            raise GitHubApiError(200, "GitHub 仓库条目不是对象")

        owner_payload = item.get("owner")
        owner = ""
        if isinstance(owner_payload, dict):
            owner = str(owner_payload.get("login", "")).strip()

        full_name = str(item.get("full_name", "")).strip()
        if not owner and "/" in full_name:
            owner = full_name.split("/", 1)[0]

        name = str(item.get("name", "")).strip()
        if not name and "/" in full_name:
            name = full_name.split("/", 1)[1]

        if not owner or not name or not full_name:
            raise GitHubApiError(200, "GitHub 仓库条目缺少 owner/name/full_name")

        return GitHubRepositoryItem(
            github_id=int(item["id"]),
            owner=owner,
            name=name,
            full_name=full_name,
            html_url=str(item.get("html_url", "")),
            description=item.get("description"),
            language=item.get("language"),
            stars=int(item.get("stargazers_count", 0)),
            forks=int(item.get("forks_count", 0)),
            open_issues=int(item.get("open_issues_count", 0)),
            default_branch=item.get("default_branch"),
            pushed_at=item.get("pushed_at"),
            github_updated_at=item.get("updated_at"),
            github_created_at=item.get("created_at"),
            is_archived=bool(item.get("archived", False)),
            is_fork=bool(item.get("fork", False)),
        )
