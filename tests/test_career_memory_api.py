"""我的求职记忆 API 的所有权和用户控制测试。"""

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.career_assistant.persistence.records import CareerMemoryItemRecord
from src.career_assistant.web import memory_router


def item(*, status="candidate"):
    now = datetime.now(UTC)
    return CareerMemoryItemRecord(
        id=uuid4(), organization_id=uuid4(), actor_id=uuid4(), career_space_id=uuid4(),
        memory_type="award", normalized_value={"summary": "一等奖"},
        display_text="获得一等奖", source_kind="explicit_user_statement", status=status,
        valid_from=now, created_at=now, updated_at=now,
    )


def client_with(repository, service=None):
    app = FastAPI()
    app.include_router(memory_router.router)
    services = SimpleNamespace(
        memory_repository=repository,
        memory_service=service or SimpleNamespace(),
    )
    actor = SimpleNamespace(organization_id=uuid4(), actor_id=uuid4())
    return app, services, actor


def test_candidate_can_be_confirmed_by_owner() -> None:
    candidate = item(status="active")

    class Repository:
        def confirm_candidate(self, organization_id, actor_id, memory_id):
            self.call = (organization_id, actor_id, memory_id)
            return candidate

    repository = Repository()
    app, services, actor = client_with(repository)
    with (
        patch.object(memory_router, "get_career_services", return_value=services),
        patch.object(memory_router, "get_request_actor", return_value=actor),
    ):
        response = TestClient(app).post(f"/api/career/memories/{candidate.id}/confirm")
    assert response.status_code == 200
    assert response.json()["item"]["status"] == "active"
    assert repository.call[0:2] == (actor.organization_id, actor.actor_id)


def test_other_actor_cannot_patch_memory() -> None:
    memory_id = uuid4()

    class Service:
        def correct(self, *args, **kwargs):
            raise LookupError("求职记忆不存在或已经失效")

    app, services, actor = client_with(SimpleNamespace(), Service())
    with (
        patch.object(memory_router, "get_career_services", return_value=services),
        patch.object(memory_router, "get_request_actor", return_value=actor),
    ):
        response = TestClient(app).patch(
            f"/api/career/memories/{memory_id}",
            json={"display_text": "越权修改", "normalized_value": {"summary": "越权修改"}},
        )
    assert response.status_code == 404


def test_deleted_memory_usage_does_not_restore_fact_text() -> None:
    class Repository:
        def list_turn_usages(self, *args):
            return ({
                "memory_id": None,
                "memory_type": "award",
                "source_kind": "explicit_user_statement",
                "created_at": datetime.now(UTC),
                "display_text": None,
                "candidate_profile_version": None,
                "candidate_profile_name": None,
                "source_conversation_created_at": None,
            },)

    app, services, actor = client_with(Repository())
    with (
        patch.object(memory_router, "get_career_services", return_value=services),
        patch.object(memory_router, "get_request_actor", return_value=actor),
    ):
        response = TestClient(app).get(f"/api/career/turns/{uuid4()}/memory-usages")
    assert response.status_code == 200
    assert response.json()["items"][0]["display_text"] == "该记忆已由用户删除"
    assert "一等奖" not in response.text
