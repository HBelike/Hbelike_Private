from __future__ import annotations

from datetime import UTC, date, datetime
from types import SimpleNamespace
from uuid import uuid4

from src.career_assistant.interview_library.models import (
    InterviewExperienceStatus,
    InterviewSourceType,
)
from src.career_assistant.interview_library.service import InterviewLibraryService


def _experience(*, company_id, company_name: str, role_name: str):
    now = datetime(2026, 8, 26, 16, tzinfo=UTC)
    return SimpleNamespace(
        id=uuid4(),
        organization_id=uuid4(),
        company_id=company_id,
        company_name=company_name,
        job_name="Agent应用开发（后端）· 日期待补充",
        role_name=role_name,
        normalized_role_name=role_name.lower(),
        interview_date=date(2026, 8, 26),
        source_type=InterviewSourceType.MANUAL_TEXT,
        source_platform="小红书",
        source_url=None,
        summary_text="原摘要",
        markdown_content="# 原正文",
        normalized_markdown="# 原正文",
        tags=("Agent",),
        status=InterviewExperienceStatus.INDEXED,
        chunking_version="v1",
        indexed_at=now,
        created_at=now,
        updated_at=now,
    )


class _Repository:
    def __init__(self) -> None:
        self.old_company_id = uuid4()
        self.new_company_id = uuid4()
        self.current = _experience(
            company_id=self.old_company_id,
            company_name="携程",
            role_name="Agent应用开发（后端）",
        )
        self.update_args = None
        self.replaced = None

    def get_experience(self, organization_id, experience_id):
        return self.current

    def upsert_company(self, organization_id, display_name):
        return SimpleNamespace(id=self.new_company_id, display_name=display_name)

    def update_experience_markdown(self, organization_id, experience_id, **kwargs):
        self.update_args = kwargs
        self.current = SimpleNamespace(
            **{
                **vars(self.current),
                "company_id": kwargs["company_id"],
                "company_name": "小红书",
                "role_name": kwargs["role_name"],
                "normalized_role_name": kwargs["role_name"].lower(),
                "markdown_content": kwargs["markdown_content"],
                "normalized_markdown": kwargs["normalized_markdown"],
                "summary_text": kwargs["summary_text"],
                "tags": kwargs["tags"],
            }
        )
        return self.current

    def replace_chunks(self, organization_id, experience_id, chunks):
        self.replaced = tuple(chunks)


class _Chunker:
    def __init__(self) -> None:
        self.context = None

    def split(self, markdown, *, company_name, role_name, job_name):
        self.context = {
            "markdown": markdown,
            "company_name": company_name,
            "role_name": role_name,
            "job_name": job_name,
        }
        return (SimpleNamespace(content_text=markdown),)


def test_update_markdown_moves_company_and_updates_role_without_changing_job_name():
    repository = _Repository()
    chunker = _Chunker()
    service = InterviewLibraryService(repository, chunker=chunker)
    original_job_name = repository.current.job_name

    updated = service.update_markdown(
        repository.current.organization_id,
        repository.current.id,
        company_name="小红书",
        role_name="大模型应用开发",
        markdown_content="# 新正文",
        summary_text="新摘要",
        tags=("RAG",),
    )

    assert repository.update_args["company_id"] == repository.new_company_id
    assert repository.update_args["role_name"] == "大模型应用开发"
    assert updated.company_name == "小红书"
    assert updated.job_name == original_job_name
    assert chunker.context == {
        "markdown": "# 新正文",
        "company_name": "小红书",
        "role_name": "大模型应用开发",
        "job_name": original_job_name,
    }
    assert repository.replaced is not None
