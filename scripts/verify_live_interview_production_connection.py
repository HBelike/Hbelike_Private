"""容器内执行一次公开 HTTPS/WSS 长连接验收；只清理本次合成会话。"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import statistics
import time
from uuid import UUID

import httpx
from sqlalchemy import text
from websockets.asyncio.client import connect

from src.career_assistant.persistence.database import CareerDatabase
from src.platform_access.repository import PlatformAccessRepository
from src.platform_access.security import create_session_token, digest_session_token


async def verify(token: str, session_id: str, *, duration: float = 75.0) -> dict:
    url = f"wss://xingxingtech.cn/api/career/live-interviews/{session_id}/stream"
    audio = base64.b64encode(bytes(4_800)).decode("ascii")
    pings: dict[str, float] = {}
    latencies: list[float] = []
    completed = asyncio.Event()
    ready = asyncio.Event()
    metrics: dict = {}
    async with connect(url, additional_headers={"Cookie": "platform_session=" + token},
                       ping_interval=10, ping_timeout=20, open_timeout=25) as websocket:
        async def read():
            async for raw in websocket:
                event = json.loads(raw)
                if event["type"] == "error":
                    raise RuntimeError("线上验收错误码：" + str(event.get("code")))
                if event["type"] == "session.ready":
                    ready.set()
                elif event["type"] == "pong" and pings:
                    _, sent = pings.popitem()
                    latencies.append((time.perf_counter() - sent) * 1000)
                elif event["type"] == "answer.delta" and event.get("delta_index") == 1:
                    metrics["model_first_token_ms"] = event.get("first_token_ms")
                elif event["type"] == "answer.completed":
                    metrics["generation_ms"] = event.get("generation_ms")
                    completed.set()

        reader = asyncio.create_task(read())
        try:
            await asyncio.wait_for(ready.wait(), timeout=25)
            started = time.perf_counter()
            await websocket.send(json.dumps({"type": "answer.request", "mode": "manual",
                                             "question": "这是合成链路验收，请只回复：链路验收通过。"}))
            frames = 0
            while time.perf_counter() - started < duration:
                if reader.done():
                    reader.result()
                    raise RuntimeError("长连接提前关闭")
                await websocket.send(json.dumps({"type": "audio.append", "channel": "interviewer",
                                                 "sequence": frames, "pcm_base64": audio}))
                if frames % 10 == 0:
                    pings[str(frames)] = time.perf_counter()
                    await websocket.send('{"type":"ping"}')
                frames += 1
                await asyncio.sleep(max(0, started + frames * 0.1 - time.perf_counter()))
            if not completed.is_set():
                raise RuntimeError("真实回答未完成")
            if len(latencies) < 70:
                raise RuntimeError("应用心跳回包不足")
            elapsed = time.perf_counter() - started
            await websocket.send('{"type":"session.end"}')
            return {"result": "ok", "duration_seconds": round(elapsed, 2), "audio_frames": frames,
                    "application_pongs": len(latencies), "pong_p50_ms": round(statistics.median(latencies), 2),
                    "pong_p95_ms": round(sorted(latencies)[int(len(latencies) * 0.95)], 2),
                    "real_model_calls": 1, "real_asr_connections": 1, **metrics}
        finally:
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)


def main() -> None:
    database = CareerDatabase(os.environ["CAREER_DATABASE_URL"])
    access = PlatformAccessRepository(database)
    token = create_session_token()
    session_id: str | None = None
    result = None
    with database.transaction() as connection:
        email = connection.execute(text(
            "SELECT email FROM career_assistant.platform_users WHERE role='admin' AND is_active LIMIT 1"
        )).scalar_one()
    user = access.find_user_for_login(email)[0]
    access.create_session(user, token, idle_ttl_hours=1, absolute_ttl_hours=1)
    try:
        with httpx.Client(base_url="https://xingxingtech.cn", timeout=30,
                          headers={"Cookie": "platform_session=" + token}) as client:
            response = client.get("/api/career/live-interviews/setup-options")
            response.raise_for_status()
            setup = response.json()
            models = [item for item in setup["answer_models"] if item["readiness"] == "ready"]
            model = next((item for item in models if "flash" in item["model_id"].lower()), models[0])
            response = client.post("/api/career/live-interviews/sessions", json={
                "client_kind": "browser", "candidate_audio_enabled": False,
                "answer_model_profile_id": model["id"],
            })
            response.raise_for_status()
            session_id = response.json()["session"]["id"]
            result = asyncio.run(verify(token, session_id))
            result["model_id"] = model["model_id"]
            result["asr_model_id"] = setup["environment_asr"]["model_id"]
    finally:
        # 结束处理完成后再删除本次唯一会话，避免与服务端 finally 写入竞争。
        time.sleep(2)
        with database.transaction() as connection:
            if session_id is not None:
                connection.execute(text("DELETE FROM career_assistant.live_interview_sessions "
                                        "WHERE id=:id AND actor_id=:actor"),
                                   {"id": UUID(session_id), "actor": user.id})
            connection.execute(text("DELETE FROM career_assistant.platform_sessions WHERE token_digest=:digest"),
                               {"digest": digest_session_token(token)})
        database.close()
    print(json.dumps({**result, "synthetic_session_cleaned": True, "auth_session_cleaned": True}))


if __name__ == "__main__":
    main()
