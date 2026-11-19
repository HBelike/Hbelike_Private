from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.career_assistant.greetings import (
    GreetingCandidateNotFoundError,
    GreetingEvidence,
    GreetingGenerationError,
    GreetingGenerationResult,
    GreetingJobValidationError,
    GreetingModelUnavailableError,
)
from src.career_assistant.web import router as career_router
from src.platform_access.contracts import PlatformRole


CANDIDATE_ID = uuid4()


def request_payload() -> dict[str, object]:
    return {
        "candidate_profile_id": str(CANDIDATE_ID),
        "job": {
            "id": "job-1",
            "title": "Java 微服务工程师",
            "company": "示例科技",
            "recruiter": "彭女士·招聘专员",
            "description": "负责 Java 微服务开发和版本交付。",
            "skills": ["Java", "微服务"],
            "source_url": "https://www.zhipin.com/job-1",
        },
        "previous_message": "",
    }


def greeting_result() -> GreetingGenerationResult:
    return GreetingGenerationResult(
        job_key="job-1",
        message=(
            "彭女士您好，我参与过银行业务系统的 Java 微服务开发与投产，也做过线上问题排查。"
            "岗位方向与我的实际经历有交集，如果方便，想进一步了解团队当前项目。"
        ),
        resume_evidence=(GreetingEvidence("CV-004", "参与 Java 微服务开发与投产"),),
        jd_highlights=(GreetingEvidence("JD-001", "负责 Java 微服务开发和版本交付"),),
        warnings=(),
        provider_key="deepseek",
        model_id="deepseek-v4-pro",
    )


def test_generate_greeting_returns_evidence_and_model() -> None:
    app = FastAPI()
    app.include_router(career_router.router)
    greeting_service = Mock()
    greeting_service.generate.return_value = greeting_result()
    services = SimpleNamespace(greeting_service=greeting_service)

    with patch.object(career_router, "get_career_services", return_value=services):
        response = TestClient(app).post(
            "/api/career/greetings/generate",
            json=request_payload(),
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["job_key"] == "job-1"
    assert payload["model"] == {
        "provider_key": "deepseek",
        "model_id": "deepseek-v4-pro",
    }
    assert payload["resume_evidence"] == [
        {"id": "CV-004", "summary": "参与 Java 微服务开发与投产"},
    ]
    called = greeting_service.generate.call_args.args
    assert called[2] == CANDIDATE_ID
    assert called[3].description == "负责 Java 微服务开发和版本交付。"


def test_admin_greeting_can_use_another_users_candidate_profile() -> None:
    app = FastAPI()
    app.include_router(career_router.router)
    greeting_service = Mock()
    greeting_service.generate.return_value = greeting_result()
    services = SimpleNamespace(greeting_service=greeting_service)
    actor = career_router.CareerRequestActor(
        organization_id=uuid4(),
        actor_id=uuid4(),
        role=PlatformRole.ADMIN,
    )

    with (
        patch.object(career_router, "get_career_services", return_value=services),
        patch.object(career_router, "get_request_actor", return_value=actor),
    ):
        response = TestClient(app).post(
            "/api/career/greetings/generate",
            json=request_payload(),
        )

    assert response.status_code == 200
    assert greeting_service.generate.call_args.kwargs["can_manage_all"] is True


def test_regeneration_does_not_reject_a_long_previous_message() -> None:
    app = FastAPI()
    app.include_router(career_router.router)
    greeting_service = Mock()
    greeting_service.generate.return_value = greeting_result()
    payload = request_payload()
    payload["previous_message"] = "旧稿" * 1_100

    with patch.object(
        career_router,
        "get_career_services",
        return_value=SimpleNamespace(greeting_service=greeting_service),
    ):
        response = TestClient(app).post("/api/career/greetings/generate", json=payload)

    assert response.status_code == 200
    assert greeting_service.generate.call_args.args[4] == payload["previous_message"]


@pytest.mark.parametrize(
    ("error", "status_code", "code"),
    [
        (GreetingCandidateNotFoundError("简历不存在"), 404, "candidate_profile_not_found"),
        (GreetingModelUnavailableError("模型不可用"), 409, "greeting_model_unavailable"),
        (GreetingJobValidationError("JD 不完整"), 422, "incomplete_job_detail"),
        (GreetingGenerationError("输出不合法"), 502, "greeting_generation_failed"),
    ],
)
def test_generate_greeting_maps_domain_errors(
    error: Exception,
    status_code: int,
    code: str,
) -> None:
    app = FastAPI()
    app.include_router(career_router.router)
    greeting_service = Mock()
    greeting_service.generate.side_effect = error
    services = SimpleNamespace(greeting_service=greeting_service)

    with patch.object(career_router, "get_career_services", return_value=services):
        response = TestClient(app).post(
            "/api/career/greetings/generate",
            json=request_payload(),
        )

    assert response.status_code == status_code
    assert response.json()["detail"] == {"code": code, "message": str(error)}


def test_generate_greeting_rejects_incomplete_job_before_service() -> None:
    app = FastAPI()
    app.include_router(career_router.router)
    greeting_service = Mock()

    with patch.object(
        career_router,
        "get_career_services",
        return_value=SimpleNamespace(greeting_service=greeting_service),
    ):
        payload = request_payload()
        payload["job"]["description"] = ""
        response = TestClient(app).post(
            "/api/career/greetings/generate",
            json=payload,
        )

    assert response.status_code == 422
    greeting_service.generate.assert_not_called()
