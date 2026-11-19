"""已上传简历的角色隔离与管理员访问契约测试。"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import BackgroundTasks

from src.career_assistant.persistence.context_repository import CareerContextRepository
from src.career_assistant.web import router as career_router
from src.platform_access.contracts import PlatformRole


def _profile(*, owner_id=None, owner_display_name="测试用户"):
    return SimpleNamespace(
        id=uuid4(),
        organization_id=uuid4(),
        actor_id=owner_id or uuid4(),
        owner_display_name=owner_display_name,
        display_name="后端开发简历",
        source_filename="resume.pdf",
        resume_outline="FastAPI 项目经验",
        version=2,
        created_at=datetime.now(UTC),
    )


class _ListResult:
    def __init__(self, rows):
        self.rows = rows

    def mappings(self):
        return self

    def all(self):
        return self.rows


class _ListConnection:
    def __init__(self, row):
        self.row = row
        self.calls = []

    def execute(self, statement, parameters):
        self.calls.append((" ".join(str(statement).split()), dict(parameters)))
        return _ListResult([self.row])


class _ListDatabase:
    def __init__(self, connection):
        self.connection = connection

    @contextmanager
    def transaction(self):
        yield self.connection


def _profile_row(*, organization_id, owner_id):
    now = datetime.now(UTC)
    return {
        "id": uuid4(),
        "organization_id": organization_id,
        "actor_id": owner_id,
        "display_name": "后端开发简历",
        "source_filename": "resume.pdf",
        "resume_outline": "FastAPI 项目经验",
        "version": 2,
        "created_at": now,
        "owner_display_name": "简历所有者",
    }


def test_repository_applies_owner_filter_only_for_normal_user_scope() -> None:
    organization_id = uuid4()
    owner_id = uuid4()
    connection = _ListConnection(_profile_row(organization_id=organization_id, owner_id=owner_id))
    repository = CareerContextRepository(_ListDatabase(connection))

    repository.list_accessible_candidate_profiles(
        organization_id,
        owner_id=owner_id,
    )
    user_sql, user_parameters = connection.calls[-1]
    repository.list_accessible_candidate_profiles(
        organization_id,
        owner_id=None,
    )
    admin_sql, admin_parameters = connection.calls[-1]

    assert "profile.organization_id = :organization_id" in user_sql
    assert "profile.actor_id = :owner_id" in user_sql
    assert user_parameters["owner_id"] == owner_id
    assert "profile.organization_id = :organization_id" in admin_sql
    assert "profile.actor_id = :owner_id" not in admin_sql
    assert admin_parameters["organization_id"] == organization_id


def test_normal_user_lists_only_own_candidate_profiles() -> None:
    actor = career_router.CareerRequestActor(
        organization_id=uuid4(),
        actor_id=uuid4(),
        role=PlatformRole.USER,
    )
    profile = _profile(owner_id=actor.actor_id)

    class Repository:
        def list_accessible_candidate_profiles(self, organization_id, *, owner_id, limit=20):
            self.call = (organization_id, owner_id, limit)
            return [profile]

    repository = Repository()
    services = SimpleNamespace(context_repository=repository)
    with (
        patch.object(career_router, "get_request_actor", return_value=actor),
        patch.object(career_router, "get_career_read_services", return_value=services),
    ):
        payload = career_router.list_candidate_profiles(SimpleNamespace())

    assert repository.call == (actor.organization_id, actor.actor_id, 20)
    assert payload["items"][0]["owner_id"] == str(actor.actor_id)


def test_admin_lists_all_candidate_profiles_in_organization() -> None:
    actor = career_router.CareerRequestActor(
        organization_id=uuid4(),
        actor_id=uuid4(),
        role=PlatformRole.ADMIN,
    )
    other_owner_id = uuid4()
    profile = _profile(owner_id=other_owner_id, owner_display_name="其他用户")

    class Repository:
        def list_accessible_candidate_profiles(self, organization_id, *, owner_id, limit=20):
            self.call = (organization_id, owner_id, limit)
            return [profile]

    repository = Repository()
    services = SimpleNamespace(context_repository=repository)
    with (
        patch.object(career_router, "get_request_actor", return_value=actor),
        patch.object(career_router, "get_career_read_services", return_value=services),
    ):
        payload = career_router.list_candidate_profiles(SimpleNamespace())

    assert repository.call == (actor.organization_id, None, 20)
    assert payload["items"][0]["owner_id"] == str(other_owner_id)
    assert payload["items"][0]["owner_display_name"] == "其他用户"


def test_admin_can_bind_another_users_candidate_profile() -> None:
    actor = career_router.CareerRequestActor(
        organization_id=uuid4(),
        actor_id=uuid4(),
        role=PlatformRole.ADMIN,
    )
    candidate_id = uuid4()
    conversation_id = uuid4()
    context = SimpleNamespace(
        binding_id=uuid4(),
        binding_version=1,
        candidate=None,
        target_role=None,
        created_at=datetime.now(UTC),
    )

    class Repository:
        def bind_conversation(
            self,
            actor_id,
            requested_conversation_id,
            candidate_profile_id,
            target_role_profile_id,
            *,
            organization_id,
            can_manage_all,
        ):
            self.call = (
                actor_id,
                requested_conversation_id,
                candidate_profile_id,
                target_role_profile_id,
                organization_id,
                can_manage_all,
            )
            return context

    repository = Repository()
    services = SimpleNamespace(
        context_repository=repository,
        job_assessment_service=SimpleNamespace(enqueue_context=lambda *_args, **_kwargs: None),
        job_assessment_repository=SimpleNamespace(),
    )
    with (
        patch.object(career_router, "get_request_actor", return_value=actor),
        patch.object(career_router, "get_career_services", return_value=services),
    ):
        career_router.update_conversation_context(
            conversation_id,
            career_router.UpdateConversationContextRequest(candidate_profile_id=candidate_id),
            SimpleNamespace(),
            BackgroundTasks(),
        )

    assert repository.call == (
        actor.actor_id,
        conversation_id,
        candidate_id,
        None,
        actor.organization_id,
        True,
    )


def test_normal_user_binding_keeps_owner_enforcement_enabled() -> None:
    actor = career_router.CareerRequestActor(
        organization_id=uuid4(),
        actor_id=uuid4(),
        role=PlatformRole.USER,
    )

    class Repository:
        def bind_conversation(self, *_args, organization_id, can_manage_all):
            self.scope = (organization_id, can_manage_all)
            raise LookupError("基准简历或目标岗位不存在，或无权访问")

    repository = Repository()
    services = SimpleNamespace(context_repository=repository)
    with (
        patch.object(career_router, "get_request_actor", return_value=actor),
        patch.object(career_router, "get_career_services", return_value=services),
    ):
        try:
            career_router.update_conversation_context(
                uuid4(),
                career_router.UpdateConversationContextRequest(candidate_profile_id=uuid4()),
                SimpleNamespace(),
                BackgroundTasks(),
            )
        except Exception as exc:
            assert getattr(exc, "status_code", None) == 404
        else:
            raise AssertionError("普通用户绑定他人简历必须失败")

    assert repository.scope == (actor.organization_id, False)
