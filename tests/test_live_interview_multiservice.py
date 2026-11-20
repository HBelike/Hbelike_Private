"""本机多服务长连接验收：独立 TCP 端口、真实 PostgreSQL 与受控 ASR/模型。"""

from __future__ import annotations

import asyncio
import base64
import json
import math
import os
import socket
import ssl
import time
from collections import Counter
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
import uvicorn
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import StreamingResponse
from sqlalchemy import text
from sqlalchemy.engine import make_url
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosedError

from src.career_assistant.live_interview import web
from src.career_assistant.model_clients import OpenAICompatibleChatClient
from src.career_assistant.model_gateway import ModelReadiness
from src.career_assistant.persistence.database import CareerDatabase


BACKGROUND = ("我们有两个消息队列。", "同一条消息可能重复到达。")
QUESTION = "如何保证消息处理幂等？"
FOLLOWUP = "它有什么缺点？"
TAIL = "谢谢，面试到这里结束。"
API_PATH = "/api/career/live-interviews"


@asynccontextmanager
async def serve(app, **tls):
    """在回环端口启动独立的 Uvicorn 服务。"""
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, lifespan="off", log_level="error",
                                              ws="websockets-sansio", access_log=False, **tls))
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(10):
                while not server.started:
                    if task.done():
                        await task
                    await asyncio.sleep(0.01)
            yield port
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, 10)


def local_tls(directory):
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=5)).not_valid_after(now + timedelta(days=1))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ip_address("127.0.0.1"))]),
                           critical=False).sign(key, hashes.SHA256()))
    certfile, keyfile = directory / "local-cert.pem", directory / "local-key.pem"
    certfile.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    keyfile.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                          serialization.NoEncryption()))
    return {"ssl_certfile": str(certfile), "ssl_keyfile": str(keyfile)}, ssl.create_default_context(cafile=str(certfile))


def make_asr_service(frames):
    app = FastAPI()

    @app.websocket("/asr")
    async def stream(ws: WebSocket):
        await ws.accept()
        start = await ws.receive_json()
        task = start["header"]["task_id"]
        await ws.send_json({"header": {"event": "task-started", "task_id": task}})
        sentence_id = -1

        async def sentence(value):
            nonlocal sentence_id
            sentence_id += 1
            await ws.send_json({
                "header": {"event": "result-generated", "task_id": task},
                "payload": {"output": {"sentence": {
                    "sentence_id": sentence_id, "text": value, "sentence_end": True,
                }}},
            })

        wants_tail = False
        while True:
            event = await ws.receive()
            if event["type"] == "websocket.disconnect":
                return
            pcm = event.get("bytes")
            if pcm is None:
                assert json.loads(event["text"])["header"]["action"] == "finish-task"
                if wants_tail:
                    await sentence(TAIL)
                await ws.send_json({"header": {"event": "task-finished", "task_id": task}})
                return
            frames[task] += 1
            assert len(pcm) == 4800
            # 通过合成 PCM 的首字节安排供应商事件，不做语言识别。
            marker = pcm[0]
            if marker == 1:
                for value in BACKGROUND:
                    await sentence(value)
            elif marker == 2:
                await sentence(QUESTION)
            elif marker == 3:
                await sentence(FOLLOWUP)
            elif marker == 4:
                number = int.from_bytes(pcm[1:3], "little")
                await sentence(f"如何处理第{number}组消息的幂等？")
            elif marker == 5:
                wants_tail = True
            elif marker == 6:
                await ws.close(code=1011, reason="模拟上游掉线")
                return
    return app


def make_model_service(prompts):
    app = FastAPI()

    @app.post("/v1/chat/completions")
    async def completion(request: Request):
        body = await request.json()
        assert body["stream"]
        prompts.append(body["messages"][-1]["content"])

        async def tokens():
            for part in ("使用业务键。", "检查重复提交。"):
                choice = {"choices": [{"delta": {"content": part}}]}
                yield f"data: {json.dumps(choice, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0.08)
            yield "data: [DONE]\n\n"
        return StreamingResponse(tokens(), media_type="text/event-stream")
    return app


def encoded_pcm(marker, number=0):
    payload = bytes([marker]) + number.to_bytes(2, "little") + bytes(4797)
    return base64.b64encode(payload).decode("ascii")


async def run_conversation(api_port, duration, session_id):
    url = f"ws://127.0.0.1:{api_port}{API_PATH}/{session_id}/stream"
    frame_count = math.ceil(duration * 10)
    events = []
    begin = time.monotonic()
    silence = encoded_pcm(0)
    async with connect(url, proxy=None, open_timeout=10, close_timeout=3) as ws:
        ready = json.loads(await asyncio.wait_for(ws.recv(), timeout=12))
        assert ready["type"] == "session.ready", ready
        begin = time.monotonic()

        async def receive():
            async for raw in ws:
                event = json.loads(raw)
                events.append((time.monotonic() - begin, event))
                if event["type"] == "session.ended":
                    break

        receiver = asyncio.create_task(receive())
        try:
            for index in range(frame_count):
                await asyncio.sleep(max(0, begin + index / 10 - time.monotonic()))
                if index in (0, 2, 15):
                    pcm = encoded_pcm({0: 1, 2: 2, 15: 3}[index])
                elif (index + 1) % 600 == 0:
                    pcm = encoded_pcm(4, (index + 1) // 600)
                else:
                    pcm = silence
                await ws.send(json.dumps({"type": "audio.append", "channel": "interviewer",
                                          "sequence": index, "pcm_base64": pcm}))
                if index % 200 == 0:
                    await ws.send('{"type":"ping"}')
                if index and index % 3000 == 0:
                    counts = Counter(event["type"] for _, event in events)
                    assert not counts["error"], [e for _, e in events if e["type"] == "error"]
                    print(json.dumps({"session": str(session_id)[:8], "elapsed": round(time.monotonic() - begin),
                                      "frames": index + 1, "pongs": counts["pong"],
                                      "completed": counts["answer.completed"]}), flush=True)
            # 最后一个完整分钟的问句及结束标记各占独立的帧。
            await ws.send(json.dumps({"type": "audio.append", "channel": "interviewer",
                                      "sequence": frame_count, "pcm_base64": encoded_pcm(5)}))
            await asyncio.sleep(max(0, begin + duration - time.monotonic()))
            await ws.send('{"type":"session.end"}')
            await asyncio.wait_for(receiver, timeout=25)
        finally:
            receiver.cancel()
            await asyncio.gather(receiver, return_exceptions=True)
    errors = [event for _, event in events if event["type"] == "error"]
    assert not errors, errors
    assert events[-1][1]["type"] == "session.ended"
    finals = [event["text"] for _, event in events if event["type"] == "transcript.final"]
    expected_questions = [f"如何处理第{number}组消息的幂等？" for number in range(1, frame_count // 600 + 1)]
    assert finals == [*BACKGROUND, QUESTION, FOLLOWUP, *expected_questions, TAIL]
    completed = [event for _, event in events if event["type"] == "answer.completed"]
    assert len(completed) >= 2 + (frame_count - 5) // 600
    versions = [event["question_version"] for event in completed]
    assert versions == list(range(1, len(completed) + 1))
    pongs = [t for t, event in events if event["type"] == "pong"]
    assert len(pongs) == math.ceil(frame_count / 200)
    return {"wall_seconds": round(time.monotonic() - begin, 2), "pcm_frames": frame_count + 1,
            "pongs": len(pongs), "finals": len(finals), "completed": len(completed),
            "completed_after_hour": sum(t >= 3600 and event["type"] == "answer.completed" for t, event in events)}


async def check_asr_reconnect(api_port, replica_port, http, key):
    url = f"ws://127.0.0.1:{api_port}{API_PATH}/{key}/stream"
    replica_url = f"ws://127.0.0.1:{replica_port}{API_PATH}/{key}/stream"

    async def assert_refused(address, code):
        async with connect(address, proxy=None) as conflicting:
            with pytest.raises(ConnectionClosedError) as closed:
                await asyncio.wait_for(conflicting.recv(), timeout=10)
            assert closed.value.rcvd.code == code

    async def audio(ws, marker, sequence):
        await ws.send(json.dumps({"type": "audio.append", "channel": "interviewer",
                                  "sequence": sequence, "pcm_base64": encoded_pcm(marker)}))

    async def until(ws, expected):
        async with asyncio.timeout(10):
            while True:
                event = json.loads(await ws.recv())
                if event["type"] == expected:
                    return event
                assert event["type"] != "error", event

    async with connect(url, proxy=None) as ws:
        await until(ws, "session.ready")
        # 第二个实例有独立连接集合，只有 PostgreSQL 能阻止其抢占活动面试。
        await assert_refused(replica_url, 4409)
        await audio(ws, 1, 0)
        await audio(ws, 2, 1)
        first = await until(ws, "answer.completed")
        await audio(ws, 6, 2)
        failure = await until(ws, "error")
        assert failure["fatal"] is True
        with pytest.raises(ConnectionClosedError) as closed:
            await ws.recv()
        assert closed.value.rcvd.code == 1013
    async with asyncio.timeout(10):
        while (await http.get(f"{API_PATH}/sessions/{key}")).json()["session"]["status"] == "active":
            await asyncio.sleep(0.05)
    async with connect(replica_url, proxy=None) as ws:
        ready = await until(ws, "session.ready")
        assert ready["question_version"] == first["question_version"]
        await assert_refused(url, 4409)
        await audio(ws, 3, 3)
        second = await until(ws, "answer.completed")
        assert second["question_version"] == first["question_version"] + 1
        await audio(ws, 5, 4)
        await ws.send('{"type":"session.end"}')
        assert (await until(ws, "transcript.final"))["text"] == TAIL
        await until(ws, "session.ended")
    await assert_refused(url, 4410)
    await assert_refused(replica_url, 4410)
    history = (await http.get(f"{API_PATH}/sessions/{key}/history")).json()
    assert history["session"]["status"] == "completed"
    assert [row["raw_text"] for row in history["utterances"]] == [*BACKGROUND, QUESTION, FOLLOWUP, TAIL]
    assert [row["question_version"] for row in history["answers"]] == [1, 2]


@pytest.mark.parametrize("mode", ["steady", "reconnect"])
def test_live_interview_multiservice_soak(monkeypatch, tmp_path: Path, mode):
    url = os.environ.get("LIVE_INTERVIEW_SOAK_DATABASE_URL")
    if not url:
        pytest.skip("设置已迁移的本机专用测试库 LIVE_INTERVIEW_SOAK_DATABASE_URL 后执行")
    database_url = make_url(url)
    assert database_url.host in {"127.0.0.1", "localhost", "::1"}
    duration = float(os.environ.get("LIVE_INTERVIEW_SOAK_SECONDS", "8"))
    concurrency = int(os.environ.get("LIVE_INTERVIEW_SOAK_CLIENTS", "2"))
    if mode == "reconnect":
        concurrency = 1
    assert 3 <= duration <= 7200 and 1 <= concurrency <= 10
    database = CareerDatabase(url)
    prompts = []
    frame_totals = Counter()
    session_ids = []
    for key, value in {"DASHSCOPE_API_KEY": "local-only", "DASHSCOPE_WORKSPACE_ID": "",
                       "PLATFORM_AUTH_REQUIRED": "false", "LIVE_INTERVIEW_FAKE_ASR": "0",
                       "LANGSMITH_TRACING": "false", "LANGCHAIN_TRACING_V2": "false"}.items():
        monkeypatch.setenv(key, value)

    async def scenario():
        tls, trust = local_tls(tmp_path)
        async with serve(make_asr_service(frame_totals)) as asr, serve(make_model_service(prompts), **tls) as model:
            monkeypatch.setenv("DASHSCOPE_ASR_WEBSOCKET_URL", f"ws://127.0.0.1:{asr}/asr")
            profile = SimpleNamespace(provider_key="openai", model_id="test-chat",
                                      api_base_url=f"https://127.0.0.1:{model}/v1")
            selected = SimpleNamespace(readiness=ModelReadiness.READY, profile=profile,
                                       credential="local-only", credential_env_name=None)
            with httpx.Client(trust_env=False, timeout=10, verify=trust) as model_http:
                def make_api(instance_database):
                    app = FastAPI()
                    app.include_router(web.router)
                    services = SimpleNamespace(database=instance_database,
                                               model_gateway=SimpleNamespace(resolve=lambda *_: selected),
                                               model_connection_client=OpenAICompatibleChatClient(client=model_http))
                    app.state.career_assistant_services = app.state.career_assistant_read_services = services
                    app.state.career_assistant_project_root = tmp_path
                    return app

                async with serve(make_api(database)) as api_port, httpx.AsyncClient(base_url=f"http://127.0.0.1:{api_port}", trust_env=False) as http:
                    for _ in range(concurrency):
                        response = await http.post(API_PATH + "/sessions", json={"client_kind": "browser", "candidate_audio_enabled": False})
                        response.raise_for_status()
                        session_ids.append(UUID(response.json()["session"]["id"]))
                    if mode == "reconnect":
                        replica_database = CareerDatabase(url)
                        try:
                            async with serve(make_api(replica_database)) as replica_port:
                                await check_asr_reconnect(api_port, replica_port, http, session_ids[0])
                        finally:
                            replica_database.close()
                        assert len(frame_totals) == 2 and len(prompts) == 2
                        assert all(term in prompts[-1] for term in (FOLLOWUP, QUESTION, "使用业务键。"))
                        return
                    results = await asyncio.gather(*(run_conversation(api_port, duration, key) for key in session_ids))
                    for key, result in zip(session_ids, results, strict=True):
                        async with asyncio.timeout(5):
                            while True:
                                history_response = await http.get(f"{API_PATH}/sessions/{key}/history")
                                history_response.raise_for_status()
                                history = history_response.json()
                                if history["session"]["status"] == "completed":
                                    break
                                await asyncio.sleep(0.03)
                        assert len(history["utterances"]) == result["finals"]
                        assert sum(answer["status"] == "completed" for answer in history["answers"]) == result["completed"]
                        assert history["answers"][0]["original_question"].endswith(QUESTION)
                        assert all(lead in history["answers"][0]["original_question"] for lead in BACKGROUND)
                    assert sorted(frame_totals.values()) == sorted(r["pcm_frames"] for r in results)
                    assert sum(FOLLOWUP in prompt and QUESTION in prompt for prompt in prompts) == concurrency
                    if duration >= 3662:
                        assert all(r["completed_after_hour"] >= 1 for r in results)
                    print(json.dumps({"duration_seconds": duration, "sessions": results, "sse_calls": len(prompts)}, ensure_ascii=False), flush=True)
    try:
        asyncio.run(scenario())
    finally:
        with database.transaction() as conn:
            for key in session_ids:
                conn.execute(text("DELETE FROM career_assistant.live_interview_sessions WHERE id = :id"), {"id": key})
        database.close()

