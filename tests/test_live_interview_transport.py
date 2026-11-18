"""本机真实 TCP/Uvicorn 验证心跳与慢 ASR，外部识别和存储用内存替身。"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import socket
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
import uvicorn
from fastapi import FastAPI, WebSocket
from starlette.websockets import WebSocketDisconnect
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from src.career_assistant.live_interview import web as live_web
from src.career_assistant.live_interview.audio_relay import AudioRelayError, LiveAudioRelay
from src.career_assistant.live_interview.contracts import (
    AudioAppendEvent,
    AudioChannel,
    AudioCommitEvent,
    ServerEvent,
    parse_client_event,
)


def audio_payload(sequence: int) -> dict[str, object]:
    return {
        "type": "audio.append",
        "channel": "interviewer",
        "sequence": sequence,
        "pcm_base64": base64.b64encode(b"\x00\x00" * 2_400).decode("ascii"),
    }


class SlowAudioManager:
    def __init__(self, *, block_seconds: float = 0.0) -> None:
        self.closed = False
        self.block_seconds = block_seconds
        self.frames: list[int] = []
        self.queue: asyncio.Queue[ServerEvent] = asyncio.Queue()
        self.started_audio = asyncio.Event()
        self.active_writes = 0

    async def start(self):
        await self.queue.put(ServerEvent("session.ready"))

    async def handle(self, event):
        if isinstance(event, AudioAppendEvent):
            self.active_writes += 1
            try:
                if not self.frames:
                    self.started_audio.set()
                    await asyncio.sleep(self.block_seconds)
                self.frames.append(event.sequence)
            finally:
                self.active_writes -= 1
        elif event.type == "ping":
            await self.queue.put(ServerEvent("pong"))
        elif event.type == "session.end":
            self.closed = True

    async def next_event(self):
        return await self.queue.get()

    async def close(self, reason):
        self.closed = True


@asynccontextmanager
async def local_transport_server(*, block_seconds: float = 0.0, baseline: bool = False):
    app = FastAPI()
    managers: list[SlowAudioManager] = []
    records = {}
    actor = SimpleNamespace(organization_id=uuid4(), actor_id=uuid4())
    repository = SimpleNamespace(
        get_session=lambda organization_id, actor_id, session_id: records.get(session_id),
        activate=lambda *args: True,
        end=lambda *args, **kwargs: True,
    )

    async def build_manager(websocket, repository, record):
        manager = SlowAudioManager(block_seconds=block_seconds)
        managers.append(manager)
        return manager

    @app.websocket("/baseline")
    async def old_transport(websocket: WebSocket):
        # 原调用链的最小复现：上游 send 挂起时不再消费浏览器 audio。
        manager = SlowAudioManager(block_seconds=block_seconds)
        managers.append(manager)
        await websocket.accept()
        await websocket.send_json({"type": "session.ready"})
        try:
            while True:
                event = parse_client_event(await websocket.receive_json())
                await manager.handle(event)
        except (WebSocketDisconnect, RuntimeError):
            pass
        finally:
            await manager.close("test")

    app.include_router(live_web.router)
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(128)
    listener.setblocking(False)
    port = listener.getsockname()[1]
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            lifespan="off",
            ws="websockets",
            ws_max_queue=2,
            ws_ping_interval=0.1,
            ws_ping_timeout=0.2,
            log_level="error",
            access_log=False,
        )
    )

    def url():
        if baseline:
            return f"ws://127.0.0.1:{port}/baseline"
        session_id = uuid4()
        records[session_id] = SimpleNamespace(
            id=session_id,
            organization_id=actor.organization_id,
            actor_id=actor.actor_id,
            candidate_audio_enabled=False,
        )
        return f"ws://127.0.0.1:{port}/api/career/live-interviews/{session_id}/stream"

    with (
        patch.object(live_web, "_resolve_websocket_actor", AsyncMock(return_value=actor)),
        patch.object(live_web, "_repository_for_websocket", return_value=repository),
        patch.object(live_web, "_build_live_manager", side_effect=build_manager),
    ):
        task = asyncio.create_task(server.serve(sockets=[listener]))
        try:
            async with asyncio.timeout(3):
                while not server.started:
                    await asyncio.sleep(0.01)
            yield url, managers
        finally:
            server.should_exit = True
            await asyncio.wait_for(task, timeout=5)
            listener.close()


def test_legacy_audio_receive_block_reproduces_keepalive_timeout():
    async def scenario():
        async with local_transport_server(block_seconds=0.65, baseline=True) as (url, _):
            with pytest.raises(ConnectionClosed, match="keepalive ping timeout"):
                async with connect(url(), ping_interval=None) as websocket:
                    await websocket.recv()
                    for index in range(20):
                        await websocket.send(json.dumps(audio_payload(index)))
                        await asyncio.sleep(0.025)
                    await asyncio.wait_for(websocket.recv(), timeout=2)

    asyncio.run(scenario())


def test_short_asr_backpressure_recovers_without_losing_audio_or_heartbeat():
    async def scenario():
        async with local_transport_server(block_seconds=0.65) as (url, managers):
            async with connect(url(), ping_interval=None) as websocket:
                assert json.loads(await websocket.recv())["type"] == "session.ready"
                for index in range(20):
                    await websocket.send(json.dumps(audio_payload(index)))
                    await asyncio.sleep(0.05)
                await websocket.send(json.dumps({"type": "ping"}))
                assert json.loads(await asyncio.wait_for(websocket.recv(), 1))["type"] == "pong"
                assert managers[0].frames == list(range(20))
                await websocket.send(json.dumps({"type": "session.end"}))
            await asyncio.sleep(0.05)
            assert managers[0].closed
            assert managers[0].active_writes == 0

    asyncio.run(scenario())


def test_asr_audio_timeout_closes_with_structured_error_and_releases_writer():
    async def scenario():
        async with local_transport_server(block_seconds=20) as (url, managers):
            async with connect(url(), ping_interval=None) as websocket:
                await websocket.recv()
                await websocket.send(json.dumps(audio_payload(0)))
                # 此时仍然能处理应用层 ping，未陷入旧版心跳超时。
                await websocket.send(json.dumps({"type": "ping"}))
                assert json.loads(await websocket.recv())["type"] == "pong"
                error = json.loads(await asyncio.wait_for(websocket.recv(), 4))
                assert error["type"] == "error"
                assert error["code"] == "asr_audio_timeout"
                with pytest.raises(ConnectionClosed) as closed:
                    await websocket.recv()
                assert closed.value.rcvd.code == 1013
            await asyncio.sleep(0.05)
            assert managers[0].closed
            assert managers[0].active_writes == 0

    asyncio.run(scenario())


def test_audio_relay_preserves_channel_fifo_and_commit_order():
    async def scenario():
        received = {channel: [] for channel in AudioChannel}

        async def handle(event):
            received[event.channel].append(event)

        relay = LiveAudioRelay(handle, tuple(AudioChannel))
        try:
            for index in range(5):
                for channel in AudioChannel:
                    relay.enqueue(AudioAppendEvent(channel, index, b"\0\0"))
            for channel in AudioChannel:
                relay.enqueue(AudioCommitEvent(channel))
            await asyncio.sleep(0.05)
            for channel in AudioChannel:
                assert [item.sequence for item in received[channel][:-1]] == list(range(5))
                assert isinstance(received[channel][-1], AudioCommitEvent)
        finally:
            await relay.close()

    asyncio.run(scenario())


def test_audio_relay_full_queue_fails_explicitly_instead_of_dropping_frames():
    async def scenario():
        async def stuck(event):
            await asyncio.Event().wait()

        relay = LiveAudioRelay(stuck, (AudioChannel.INTERVIEWER,), max_frames=2)
        try:
            for index in range(2):
                relay.enqueue(AudioAppendEvent(AudioChannel.INTERVIEWER, index, b"\0\0"))
            with pytest.raises(AudioRelayError) as error:
                relay.enqueue(AudioAppendEvent(AudioChannel.INTERVIEWER, 2, b"\0\0"))
            assert error.value.code == "asr_audio_backpressure"
            with pytest.raises(AudioRelayError):
                await relay.wait_failed()
        finally:
            await relay.close()

    asyncio.run(scenario())


def test_full_audio_queue_closes_real_connection_with_structured_error():
    async def scenario():
        async with local_transport_server(block_seconds=20) as (url, managers):
            async with connect(url(), ping_interval=None) as websocket:
                await websocket.recv()
                for index in range(30):
                    await websocket.send(json.dumps(audio_payload(index)))
                error = json.loads(await asyncio.wait_for(websocket.recv(), 2))
                assert error["code"] == "asr_audio_backpressure"
                with pytest.raises(ConnectionClosed) as closed:
                    await websocket.recv()
                assert closed.value.rcvd.code == 1013
            await asyncio.sleep(0.05)
            assert managers[0].closed
            assert managers[0].active_writes == 0

    asyncio.run(scenario())


def test_event_sender_failure_terminates_transport_and_cancels_audio_writers():
    async def scenario():
        manager = SlowAudioManager(block_seconds=0)
        await manager.start()
        receiver_cancelled = asyncio.Event()

        async def receive():
            try:
                await asyncio.Event().wait()
            finally:
                receiver_cancelled.set()

        websocket = SimpleNamespace(
            receive_json=receive,
            send_json=AsyncMock(side_effect=RuntimeError("sender failed")),
        )
        with pytest.raises(RuntimeError, match="sender failed"):
            await live_web._run_live_transport(websocket, manager, (AudioChannel.INTERVIEWER,))
        assert receiver_cancelled.is_set()
        assert not [
            task for task in asyncio.all_tasks()
            if task is not asyncio.current_task() and task.get_name().startswith("live-")
        ]

    asyncio.run(scenario())


@pytest.mark.skipif(
    not os.getenv("LIVE_INTERVIEW_TRANSPORT_SOAK_SECONDS"),
    reason="持续连接压测按需显式运行，避免拖慢常规回归。",
)
def test_real_transport_concurrent_soak():
    async def scenario():
        soak_seconds = float(os.environ["LIVE_INTERVIEW_TRANSPORT_SOAK_SECONDS"])
        for concurrency in (1, 8, 32):
            duration = soak_seconds if concurrency == 1 else 3.0
            async with local_transport_server(block_seconds=0.65) as (url, managers):
                samples = []

                async def connection():
                    async with connect(url(), ping_interval=None) as websocket:
                        assert json.loads(await websocket.recv())["type"] == "session.ready"
                        started = time.perf_counter()
                        sequence = 0
                        pongs = 0
                        while time.perf_counter() - started < duration:
                            await websocket.send(json.dumps(audio_payload(sequence)))
                            sequence += 1
                            if sequence % 10 == 0:
                                ping_at = time.perf_counter()
                                await websocket.send(json.dumps({"type": "ping"}))
                                assert json.loads(await asyncio.wait_for(websocket.recv(), 1))["type"] == "pong"
                                samples.append((time.perf_counter() - ping_at) * 1_000)
                                pongs += 1
                            await asyncio.sleep(0.1)
                        await asyncio.sleep(0.15)
                        await websocket.send(json.dumps({"type": "session.end"}))
                        return sequence, pongs

                results = await asyncio.gather(*(connection() for _ in range(concurrency)))
                await asyncio.sleep(0.1)
                assert all(manager.closed and manager.active_writes == 0 for manager in managers)
                assert sum(len(manager.frames) for manager in managers) == sum(row[0] for row in results)
                ordered = sorted(samples)
                print(json.dumps({
                    "concurrency": concurrency,
                    "duration_seconds": duration,
                    "frames": sum(row[0] for row in results),
                    "pongs": sum(row[1] for row in results),
                    "pong_p95_ms": round(ordered[max(0, int(len(ordered) * 0.95) - 1)], 3),
                    "disconnects": 0,
                }))

    asyncio.run(scenario())
