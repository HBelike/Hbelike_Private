"""全网公开面经永久账本和来源仓储测试。"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from uuid import UUID, uuid4

from src.career_assistant.interview_library.models import InterviewWebDocumentStatus
from src.career_assistant.interview_library.repository import InterviewLibraryRepository


ORG_ID = UUID("00000000-0000-0000-0000-000000000151")
DOCUMENT_ID = UUID("00000000-0000-0000-0000-000000000152")
EXPERIENCE_ID = UUID("00000000-0000-0000-0000-000000000153")
NOW = datetime(2026, 8, 27, 8, 0, tzinfo=UTC)


class FakeResult:
    def __init__(self, *, row=None, rows=None, rowcount: int = 1) -> None:
        self.row = row
        self.rows = list(rows or [])
        self.rowcount = rowcount

    def mappings(self):
        return self

    def one(self):
        return self.row

    def one_or_none(self):
        return self.row

    def all(self):
        return self.rows


class FakeConnection:
    def __init__(self, results) -> None:  # type: ignore[no-untyped-def]
        self.results = list(results)
        self.calls: list[tuple[str, dict[str, object]]] = []

    def execute(self, statement, parameters=None):  # type: ignore[no-untyped-def]
        self.calls.append((" ".join(str(statement).split()), dict(parameters or {})))
        return self.results.pop(0)


class FakeDatabase:
    def __init__(self, results) -> None:  # type: ignore[no-untyped-def]
        self.connection = FakeConnection(results)

    @contextmanager
    def transaction(self):
        yield self.connection


def web_document_row(**overrides):  # type: ignore[no-untyped-def]
    value = {
        "id": DOCUMENT_ID,
        "organization_id": ORG_ID,
        "canonical_url": "https://example.com/agent",
        "source_url": "https://example.com/agent?utm_source=search",
        "source_platform": "example.com",
        "title": "Agent 面经",
        "search_snippet": "一面题目",
        "status": "fetching",
        "normalized_markdown": None,
        "content_hash": None,
        "imported_experience_id": None,
        "attempt_count": 1,
        "error_code": None,
        "error_message": None,
        "first_seen_at": NOW,
        "last_seen_at": NOW,
        "last_fetched_at": None,
        "next_retry_at": None,
        "metadata_json": {"latest_keyword": "agent开发面经"},
        "created_at": NOW,
        "updated_at": NOW,
    }
    value.update(overrides)
    return value


def source_row(**overrides):  # type: ignore[no-untyped-def]
    value = {
        "id": uuid4(),
        "organization_id": ORG_ID,
        "experience_id": EXPERIENCE_ID,
        "canonical_url": "https://mirror.example/agent",
        "source_url": "https://mirror.example/agent?utm_source=search",
        "source_platform": "mirror.example",
        "is_primary": False,
        "discovery_keywords_json": ["agent开发面经"],
        "first_seen_at": NOW,
        "last_seen_at": NOW,
        "created_at": NOW,
        "updated_at": NOW,
    }
    value.update(overrides)
    return value


def test_register_web_document_is_org_scoped_and_preserves_terminal_status() -> None:
    database = FakeDatabase([FakeResult(row=web_document_row(status="imported"))])
    repository = InterviewLibraryRepository(database)

    record = repository.register_web_document(
        ORG_ID,
        canonical_url="https://example.com/agent",
        source_url="https://example.com/agent?utm_source=search",
        source_platform="example.com",
        title="Agent 面经",
        search_snippet="一面题目",
        keyword="agent开发面经",
        search_rank=1,
    )

    sql, params = database.connection.calls[0]
    assert "ON CONFLICT (organization_id, canonical_url) DO UPDATE" in sql
    assert "status = interview_web_documents.status" in sql
    assert params["organization_id"] == ORG_ID
    assert record.status is InterviewWebDocumentStatus.IMPORTED


def test_claim_web_document_is_organization_scoped_and_atomic() -> None:
    database = FakeDatabase([FakeResult(row=web_document_row())])
    repository = InterviewLibraryRepository(database)

    claimed = repository.claim_web_document(ORG_ID, DOCUMENT_ID, now=NOW)

    sql, params = database.connection.calls[0]
    assert "UPDATE career_assistant.interview_web_documents" in sql
    assert "status IN ('discovered', 'retryable_failed')" in sql
    assert "next_retry_at IS NULL OR next_retry_at <= :now" in sql
    assert "organization_id = :organization_id" in sql
    assert claimed is not None and claimed.status is InterviewWebDocumentStatus.FETCHING
    assert params["organization_id"] == ORG_ID


def test_mark_failed_uses_three_step_retry_and_never_resets_attempts() -> None:
    database = FakeDatabase(
        [FakeResult(row=web_document_row(status="retryable_failed", next_retry_at=NOW))],
    )
    repository = InterviewLibraryRepository(database)

    record = repository.mark_web_document_failed(
        ORG_ID,
        DOCUMENT_ID,
        error_code="firecrawl_timeout",
        error_message="公开网页服务响应超时，请稍后重试。",
        retryable=True,
        now=NOW,
    )

    sql, params = database.connection.calls[0]
    assert "attempt_count <= 3" in sql
    assert "INTERVAL '1 hour'" in sql
    assert "INTERVAL '6 hours'" in sql
    assert "INTERVAL '24 hours'" in sql
    assert "attempt_count = attempt_count" in sql
    assert params["retryable"] is True
    assert record.status is InterviewWebDocumentStatus.RETRYABLE_FAILED


def test_add_duplicate_source_does_not_replace_primary() -> None:
    database = FakeDatabase([FakeResult(row=source_row())])
    repository = InterviewLibraryRepository(database)

    source = repository.add_experience_source(
        ORG_ID,
        EXPERIENCE_ID,
        canonical_url="https://mirror.example/agent",
        source_url="https://mirror.example/agent?utm_source=search",
        source_platform="mirror.example",
        keyword="agent开发面经",
        is_primary=False,
    )

    sql, params = database.connection.calls[0]
    assert "ON CONFLICT (organization_id, canonical_url) DO UPDATE" in sql
    assert "WHEN interview_experience_sources.is_primary THEN TRUE" in sql
    assert "AND NOT EXISTS" in sql
    assert params["organization_id"] == ORG_ID
    assert source.is_primary is False


def test_list_sources_requires_organization_and_experience() -> None:
    database = FakeDatabase([FakeResult(rows=[source_row(is_primary=True)])])
    repository = InterviewLibraryRepository(database)

    sources = repository.list_experience_sources(ORG_ID, EXPERIENCE_ID)

    sql, params = database.connection.calls[0]
    assert "organization_id = :organization_id" in sql
    assert "experience_id = :experience_id" in sql
    assert "is_primary DESC, first_seen_at ASC" in sql
    assert params == {"organization_id": ORG_ID, "experience_id": EXPERIENCE_ID}
    assert sources[0].is_primary is True


def test_list_web_documents_by_hash_stays_inside_organization() -> None:
    database = FakeDatabase([FakeResult(rows=[web_document_row(content_hash="a" * 64)])])
    repository = InterviewLibraryRepository(database)

    documents = repository.list_web_documents_by_content_hash(ORG_ID, "a" * 64)

    sql, params = database.connection.calls[0]
    assert "organization_id = :organization_id" in sql
    assert "content_hash = :content_hash" in sql
    assert params["organization_id"] == ORG_ID
    assert documents[0].content_hash == "a" * 64
