from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.career_assistant.live_interview.contracts import ServerEvent
from src.career_assistant.live_interview.persistence import session_payload
from src.career_assistant.live_interview import web as live_web
from src.career_assistant.live_interview.contracts import LiveInterviewStatus


class FakeRepository:
    def __init__(self, record) -> None:
        self.record = record

    def get_session(self, organization_id, actor_id, session_id):
        if session_id == self.record.id:
            return self.record
        return None

    def activate(self, organization_id, actor_id, session_id):
        return True

    def end(self, organization_id, actor_id, session_id, *, failed=False):
        return True


class FakeManager:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[ServerEvent] = asyncio.Queue()
        self.closed = False

    async def start(self):
        await self.queue.put(ServerEvent("session.ready", {"sample_rate": 24000}))

    async def handle(self, event):
        if event.type == "ping":
            await self.queue.put(ServerEvent("pong"))
        if event.type == "session.end":
            self.closed = True

    async def next_event(self):
        return await self.queue.get()

    async def close(self, reason):
        self.closed = True


def _record():
    from datetime import UTC, datetime
    from src.career_assistant.live_interview.persistence import LiveInterviewSessionRecord

    now = datetime.now(UTC)
    return LiveInterviewSessionRecord(
        id=uuid4(),
        organization_id=live_web.DEFAULT_ORGANIZATION_ID,
        actor_id=live_web.DEFAULT_ACTOR_ID,
        candidate_profile_id=uuid4(),
        target_role_profile_id=uuid4(),
        interview_experience_ids=(),
        asr_provider="fake",
        asr_model_profile_id=None,
        answer_model_profile_id=None,
        status=LiveInterviewStatus.PREPARING,
        started_at=None,
        ended_at=None,
        created_at=now,
        updated_at=now,
    )


def test_session_payload_never_contains_audio() -> None:
    payload = session_payload(_record())
    assert "pcm" not in str(payload).lower()
    assert "audio" not in str(payload).lower()


def test_session_can_start_without_reference_materials() -> None:
    app = FastAPI()
    app.include_router(live_web.router)
    repository = SimpleNamespace()
    repository.create_session = lambda *args, **kwargs: replace(
        _record(),
        candidate_profile_id=None,
        target_role_profile_id=None,
        interview_experience_ids=(),
        asr_model_profile_id=kwargs["asr_model_profile_id"],
        answer_model_profile_id=kwargs["answer_model_profile_id"],
    )
    asr_model_id = uuid4()
    answer_model_id = uuid4()

    with (
        patch.object(live_web, "get_live_repository", return_value=repository),
        TestClient(app) as client,
    ):
        response = client.post(
            "/api/career/live-interviews/sessions",
            json={
                "asr_model_profile_id": str(asr_model_id),
                "answer_model_profile_id": str(answer_model_id),
            },
        )

    assert response.status_code == 201
    assert response.json()["session"]["candidate_profile_id"] is None
    assert response.json()["session"]["target_role_profile_id"] is None


def test_session_rejects_missing_transcription_configuration() -> None:
    app = FastAPI()
    app.include_router(live_web.router)

    with (
        patch.dict(os.environ, {"OPENAI_API_KEY": ""}),
        TestClient(app) as client,
    ):
        response = client.post(
            "/api/career/live-interviews/sessions",
            json={"answer_model_profile_id": str(uuid4())},
        )

    assert response.status_code == 422
    assert "实时转写模型" in response.json()["detail"]


def test_desktop_launch_endpoint_starts_windows_capture_tool() -> None:
    app = FastAPI()
    app.include_router(live_web.router)
    result = SimpleNamespace(status="launching", message="面试大师正在启动")

    with (
        patch.object(live_web, "launch_windows_desktop_assistant", return_value=result) as launch,
        TestClient(app) as client,
    ):
        response = client.post("/api/career/live-interviews/desktop/launch")

    assert response.status_code == 200
    assert response.json() == {"status": "launching", "message": "面试大师正在启动"}
    launch.assert_called_once_with(api_base_url="http://testserver")


def test_desktop_launch_endpoint_returns_actionable_error() -> None:
    app = FastAPI()
    app.include_router(live_web.router)

    with (
        patch.object(
            live_web,
            "launch_windows_desktop_assistant",
            side_effect=live_web.DesktopLauncherError("采集组件尚未安装完成"),
        ),
        TestClient(app) as client,
    ):
        response = client.post("/api/career/live-interviews/desktop/launch")

    assert response.status_code == 409
    assert response.json()["detail"] == "采集组件尚未安装完成"


def test_websocket_accepts_ping_and_ends_cleanly_in_local_auth_mode() -> None:
    record = _record()
    repository = FakeRepository(record)
    manager = FakeManager()
    app = FastAPI()
    app.include_router(live_web.router)

    with (
        patch.object(live_web, "_repository_for_websocket", return_value=repository),
        patch.object(live_web, "_build_live_manager", new=AsyncMock(return_value=manager)),
        TestClient(app) as client,
    ):
        with client.websocket_connect(f"/api/career/live-interviews/{record.id}/stream") as ws:
            assert ws.receive_json()["type"] == "session.ready"
            ws.send_json({"type": "ping"})
            assert ws.receive_json()["type"] == "pong"
            ws.send_json({"type": "session.end"})

    assert manager.closed


def test_websocket_rejects_missing_session_with_4404() -> None:
    record = _record()
    app = FastAPI()
    app.include_router(live_web.router)
    with (
        patch.object(live_web, "_repository_for_websocket", return_value=FakeRepository(record)),
        TestClient(app) as client,
    ):
        missing_id = uuid4()
        try:
            with client.websocket_connect(f"/api/career/live-interviews/{missing_id}/stream"):
                raise AssertionError("不存在的会话不应连接成功")
        except WebSocketDisconnect as exc:
            assert exc.code == 4404
