"""面经库公开资料采集的合规编排层。

本模块只负责两类输入：用户明确提供的公开 URL，和平台关键词检索任务的状态管理。
它不保存第三方账号、密码或 Cookie，也不尝试绕过验证码、反爬机制或访问控制。平台
关键词任务在没有已获授权的官方 API/浏览器连接器时会明确停在
``needs_user_interaction``，而不是伪造“已抓到正文”的结果。
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from html.parser import HTMLParser
import ipaddress
import socket
from typing import Final
from urllib.parse import urlparse
from urllib.request import Request, urlopen
from uuid import UUID

from src.career_assistant.interview_library.models import (
    CollectionCandidateStatus,
    CollectionConnectorKind,
    CollectionJobStatus,
    InterviewCollectionCandidateRecord,
    InterviewCollectionJobRecord,
    InterviewSourceType,
)
from src.career_assistant.interview_library.repository import InterviewLibraryRepository
from src.career_assistant.interview_library.service import (
    InterviewExperienceDraft,
    InterviewLibraryService,
)
from src.career_assistant.interview_library.models import IngestionTriggerType


MAX_ARTICLE_BYTES: Final[int] = 3 * 1024 * 1024
MAX_ARTICLE_CHARACTERS: Final[int] = 260_000


class CollectionOperationError(RuntimeError):
    """向路由层传递可展示、不可泄露内部细节的采集错误。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class PlatformCollectionPolicy:
    """前端展示和服务端执行共用的平台采集边界。"""

    key: str
    label: str
    can_run_keyword_search: bool
    connector_kind: CollectionConnectorKind
    policy_decision: str


@dataclass(frozen=True)
class ExtractedPublicArticle:
    """从一个用户明确提交的公开页面中安全抽取的文本结果。"""

    source_url: str
    canonical_url: str
    title: str | None
    markdown_content: str


class _ArticleTextParser(HTMLParser):
    """最小依赖的 HTML 正文归一器，不保留原始 HTML、脚本或样式。"""

    _SKIPPED_TAGS = {"script", "style", "noscript", "svg", "canvas", "template"}
    _BLOCK_TAGS = {"article", "main", "section", "p", "li", "h1", "h2", "h3", "h4", "br", "div"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._skip_depth = 0
        self._in_title = False
        self._title_parts: list[str] = []
        self._parts: list[str] = []

    @property
    def title(self) -> str | None:
        value = " ".join(" ".join(self._title_parts).split()).strip()
        return value[:300] or None

    @property
    def text(self) -> str:
        normalized_lines: list[str] = []
        for item in "\n".join(self._parts).splitlines():
            compact = " ".join(item.split()).strip()
            if not compact:
                continue
            if compact.lower() in {"登录", "注册", "下载 app", "打开 app", "举报", "cookie 设置"}:
                continue
            if not normalized_lines or normalized_lines[-1] != compact:
                normalized_lines.append(compact)
        return "\n\n".join(normalized_lines)

    def handle_starttag(self, tag: str, attrs) -> None:  # type: ignore[no-untyped-def]
        normalized_tag = tag.lower()
        if normalized_tag in self._SKIPPED_TAGS:
            self._skip_depth += 1
            return
        if normalized_tag == "title":
            self._in_title = True
        if not self._skip_depth and normalized_tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        normalized_tag = tag.lower()
        if normalized_tag in self._SKIPPED_TAGS and self._skip_depth:
            self._skip_depth -= 1
            return
        if normalized_tag == "title":
            self._in_title = False
        if not self._skip_depth and normalized_tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self._title_parts.append(data)
        self._parts.append(data)


class PublicUrlArticleExtractor:
    """提取用户提交的公开 HTTPS URL，并在请求前执行基础 SSRF 防护。

    此类不是通用爬虫：不执行 JavaScript、不携带用户会话、不模拟登录、不处理验证码。
    对 JS 渲染页、受限页或空正文会返回明确的可恢复错误。
    """

    def extract(self, source_url: str) -> ExtractedPublicArticle:
        self._validate_public_https_url(source_url)
        request = Request(
            source_url,
            headers={
                "User-Agent": "InterviewLibraryUrlImport/1.0 (+user-submitted-public-url)",
                "Accept": "text/html,application/xhtml+xml",
            },
            method="GET",
        )
        try:
            with urlopen(request, timeout=15) as response:  # noqa: S310 - URL 已做协议与私网校验
                final_url = response.geturl()
                self._validate_public_https_url(final_url)
                content_type = response.headers.get_content_type().lower()
                if content_type not in {"text/html", "application/xhtml+xml"}:
                    raise CollectionOperationError(
                        "unsupported_content_type",
                        "该链接不是可解析的 HTML 页面，请改用粘贴正文或上传文件。",
                    )
                charset = response.headers.get_content_charset() or "utf-8"
                payload = response.read(MAX_ARTICLE_BYTES + 1)
        except CollectionOperationError:
            raise
        except TimeoutError as exc:
            raise CollectionOperationError("fetch_timeout", "页面读取超时，请稍后重试或粘贴正文。") from exc
        except OSError as exc:
            raise CollectionOperationError(
                "fetch_unavailable",
                "暂时无法读取该公开页面；它可能需要登录、限制了访问，或网络不可达。",
            ) from exc

        if len(payload) > MAX_ARTICLE_BYTES:
            raise CollectionOperationError("response_too_large", "页面内容过大，请改用粘贴正文或导入文件。")

        decoded = payload.decode(charset, errors="replace")
        parser = _ArticleTextParser()
        try:
            parser.feed(decoded)
            parser.close()
        except Exception as exc:  # pragma: no cover - HTMLParser 对畸形页面通常可恢复
            raise CollectionOperationError("html_parse_failed", "页面结构异常，暂时无法提取正文。") from exc

        text = parser.text[:MAX_ARTICLE_CHARACTERS].strip()
        if len(text) < 80:
            raise CollectionOperationError(
                "article_empty",
                "未从页面提取到足够正文；该站点可能依赖登录或前端渲染，请粘贴正文后导入。",
            )
        title = parser.title
        markdown = f"# {title}\n\n{text}" if title else text
        return ExtractedPublicArticle(
            source_url=source_url,
            canonical_url=final_url,
            title=title,
            markdown_content=markdown,
        )

    @staticmethod
    def _validate_public_https_url(value: str) -> None:
        parsed = urlparse(value)
        if parsed.scheme.lower() != "https" or not parsed.hostname:
            raise CollectionOperationError("invalid_url", "仅支持用户明确提交的公开 HTTPS 链接。")
        if parsed.username or parsed.password:
            raise CollectionOperationError("invalid_url", "链接不能包含账号或密码。")
        try:
            port = parsed.port
        except ValueError as exc:
            raise CollectionOperationError("invalid_url", "链接端口格式无效。") from exc
        if port not in {None, 443}:
            raise CollectionOperationError("unsafe_url", "公开链接仅允许使用标准 HTTPS 端口。")

        hostname = parsed.hostname.lower().rstrip(".")
        if hostname in {"localhost", "localhost.localdomain"}:
            raise CollectionOperationError("unsafe_url", "不能读取本机或内网地址。")
        try:
            addresses = {info[4][0] for info in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)}
        except socket.gaierror as exc:
            raise CollectionOperationError("dns_unavailable", "无法解析链接域名，请检查地址后重试。") from exc
        if not addresses:
            raise CollectionOperationError("dns_unavailable", "无法解析链接域名，请检查地址后重试。")
        for address in addresses:
            try:
                candidate = ipaddress.ip_address(address)
            except ValueError as exc:
                raise CollectionOperationError("unsafe_url", "链接域名解析结果无效。") from exc
            if (
                candidate.is_private
                or candidate.is_loopback
                or candidate.is_link_local
                or candidate.is_multicast
                or candidate.is_reserved
                or candidate.is_unspecified
            ):
                raise CollectionOperationError("unsafe_url", "不能读取本机、内网或保留地址。")


class InterviewCollectionService:
    """编排候选发现、公开 URL 抽取与“候选后入库”流程。

    所有网络读取都发生在短生命周期方法中，数据库事务只覆盖状态/文本写入；因此
    不会把慢网页、模型调用或用户交互锁在 PostgreSQL 事务里。
    """

    _POLICIES: Final[dict[str, PlatformCollectionPolicy]] = {
        "xiaohongshu": PlatformCollectionPolicy(
            key="xiaohongshu",
            label="小红书",
            can_run_keyword_search=False,
            connector_kind=CollectionConnectorKind.USER_AUTHORIZED_BROWSER,
            policy_decision="需要平台允许的用户授权会话或官方能力；当前不保存账号密码或 Cookie。",
        ),
        "nowcoder": PlatformCollectionPolicy(
            key="nowcoder",
            label="牛客",
            can_run_keyword_search=False,
            connector_kind=CollectionConnectorKind.USER_AUTHORIZED_BROWSER,
            policy_decision="需要平台允许的用户授权会话或官方能力；当前不保存账号密码或 Cookie。",
        ),
        "maimai": PlatformCollectionPolicy(
            key="maimai",
            label="脉脉",
            can_run_keyword_search=False,
            connector_kind=CollectionConnectorKind.USER_AUTHORIZED_BROWSER,
            policy_decision="需要平台允许的用户授权会话或官方能力；当前不保存账号密码或 Cookie。",
        ),
        "public_url": PlatformCollectionPolicy(
            key="public_url",
            label="公开链接",
            can_run_keyword_search=False,
            connector_kind=CollectionConnectorKind.URL_IMPORT,
            policy_decision="只读取用户明确提交的公开 HTTPS 页面，不执行登录、验证码绕过或会话复用。",
        ),
    }

    def __init__(
        self,
        repository: InterviewLibraryRepository,
        library_service: InterviewLibraryService,
        *,
        article_extractor: PublicUrlArticleExtractor | None = None,
    ) -> None:
        self._repository = repository
        self._library_service = library_service
        self._article_extractor = article_extractor or PublicUrlArticleExtractor()

    def list_platform_policies(self) -> list[PlatformCollectionPolicy]:
        """返回可展示的采集平台能力，不暴露内部密钥或会话引用。"""

        return list(self._POLICIES.values())

    def create_keyword_collection_job(
        self,
        organization_id: UUID,
        *,
        platform_key: str,
        keyword: str,
        requested_limit: int,
    ) -> InterviewCollectionJobRecord:
        """创建平台关键词任务；无合规连接器时明确等待用户授权而非伪造采集结果。"""

        policy = self._POLICIES.get(platform_key.strip().lower())
        if policy is None or policy.key == "public_url":
            raise ValueError("请选择小红书、牛客或脉脉等已登记的平台。")
        job = self._repository.create_collection_job(
            organization_id=organization_id,
            platform_key=policy.key,
            keyword=keyword,
            requested_limit=requested_limit,
            connector_kind=policy.connector_kind,
            policy_decision=policy.policy_decision,
        )
        if not policy.can_run_keyword_search:
            return self._repository.update_collection_job_status(
                organization_id,
                job.id,
                status=CollectionJobStatus.NEEDS_USER_INTERACTION,
                error_code="connector_not_authorized",
                error_message="该平台尚未接入官方 API 或用户授权浏览器连接器，不能自动检索正文。",
            )
        return job

    def collect_public_url(
        self,
        organization_id: UUID,
        *,
        source_url: str,
    ) -> tuple[InterviewCollectionJobRecord, InterviewCollectionCandidateRecord]:
        """读取用户明确提交的公开文章，生成待选择候选项。"""

        job = self._repository.create_collection_job(
            organization_id=organization_id,
            platform_key="public_url",
            keyword=source_url,
            requested_limit=1,
            connector_kind=CollectionConnectorKind.URL_IMPORT,
            policy_decision=self._POLICIES["public_url"].policy_decision,
        )
        self._repository.update_collection_job_status(
            organization_id,
            job.id,
            status=CollectionJobStatus.RUNNING,
        )
        try:
            article = self._article_extractor.extract(source_url)
            platform = self._infer_platform(article.canonical_url)
            content_hash = sha256(article.markdown_content.encode("utf-8")).hexdigest()
            candidate = self._repository.create_collection_candidate(
                organization_id,
                collection_job_id=job.id,
                source_url=article.source_url,
                canonical_url=article.canonical_url,
                source_platform=platform,
                title=article.title,
                snippet=article.markdown_content.replace("\n", " ")[:500],
                extracted_markdown=article.markdown_content,
                content_hash=content_hash,
                status=CollectionCandidateStatus.FETCHED,
            )
            completed = self._repository.update_collection_job_status(
                organization_id,
                job.id,
                status=CollectionJobStatus.SUCCEEDED,
            )
            return completed, candidate
        except CollectionOperationError as exc:
            self._repository.update_collection_job_status(
                organization_id,
                job.id,
                status=CollectionJobStatus.FAILED,
                error_code=exc.code,
                error_message=exc.message,
            )
            raise

    def select_candidate(
        self,
        organization_id: UUID,
        candidate_id: UUID,
    ) -> InterviewCollectionCandidateRecord:
        """把已读取的候选标记为待入库，供页面填补公司/岗位/日期。"""

        candidate = self._repository.get_collection_candidate(organization_id, candidate_id)
        if candidate is None:
            raise LookupError("候选资料不存在或无访问权限")
        if not candidate.extracted_markdown:
            raise ValueError("候选资料尚未获得可用正文，不能入库。")
        return self._repository.set_collection_candidate_status(
            organization_id,
            candidate_id,
            status=CollectionCandidateStatus.SELECTED,
        )

    def ingest_selected_candidate(
        self,
        organization_id: UUID,
        *,
        candidate_id: UUID,
        company_name: str,
        role_name: str,
        interview_date,
        summary_text: str | None,
        tags: tuple[str, ...],
    ):
        """将用户选择的候选正文转换为 Markdown 面经并进入既有 RAG 流程。"""

        candidate = self.select_candidate(organization_id, candidate_id)
        if not candidate.extracted_markdown:
            raise ValueError("候选资料未包含可入库正文。")
        experience = self._library_service.ingest(
            organization_id,
            InterviewExperienceDraft(
                company_name=company_name,
                role_name=role_name,
                interview_date=interview_date,
                markdown_content=candidate.extracted_markdown,
                source_type=InterviewSourceType.PUBLIC_URL,
                source_platform=candidate.source_platform,
                source_url=candidate.canonical_url,
                summary_text=summary_text or candidate.title,
                tags=tags,
            ),
            trigger_type=IngestionTriggerType.MANUAL_URL,
        )
        self._repository.set_collection_candidate_status(
            organization_id,
            candidate.id,
            status=CollectionCandidateStatus.IMPORTED,
        )
        return experience

    @classmethod
    def _infer_platform(cls, source_url: str) -> str:
        hostname = (urlparse(source_url).hostname or "").lower()
        labels = {
            "xiaohongshu.com": "小红书",
            "nowcoder.com": "牛客",
            "maimai.cn": "脉脉",
            "zhihu.com": "知乎",
        }
        for domain, label in labels.items():
            if hostname == domain or hostname.endswith(f".{domain}"):
                return label
        return hostname or "公开网页"
