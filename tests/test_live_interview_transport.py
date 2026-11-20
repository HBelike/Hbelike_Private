"""本机真实 TCP/Uvicorn 验证心跳与慢 ASR，外部识别和存储用内存替身。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import socket
import time
from contextlib import asynccontextmanager
from functools import partial
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
    LiveInterviewStatus,
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
        self.frames_by_channel = {channel: [] for channel in AudioChannel}
        self.pcm_digest_by_channel = {channel: hashlib.sha256() for channel in AudioChannel}
        self.queue: asyncio.Queue[ServerEvent] = asyncio.Queue()
        self.started_audio = asyncio.Event()
        self.active_writes = 0
        self.max_active_writes = 0

    async def start(self):
        await self.queue.put(ServerEvent("session.ready"))
        if getattr(self, "fatal_code", ""):
            await self.queue.put(ServerEvent("error", {"code": self.fatal_code, "fatal": True}))

    async def handle(self, event):
        if isinstance(event, AudioAppendEvent):
            self.active_writes += 1
            self.max_active_writes = max(self.max_active_writes, self.active_writes)
            try:
                if not self.frames_by_channel[event.channel]:
                    self.started_audio.set()
                    await asyncio.sleep(self.block_seconds)
                self.frames.append(event.sequence)
                self.frames_by_channel[event.channel].append(event.sequence)
                self.pcm_digest_by_channel[event.channel].update(event.pcm)
            finally:
                self.active_writes -= 1
        elif event.type == "ping":
            await self.queue.put(ServerEvent("pong"))
        elif event.type in ("session.end", "session.pause"):
            self.closed = True
            await self.queue.put(ServerEvent("session.ended"))

    async def disable_channel(self, channel, message):
        await self.queue.put(ServerEvent("channel.ended", {"channel": channel.value, "message": message}))

    async def finish_channel(self, channel):
        await self.disable_channel(channel, "结束可选音轨")

    async def next_event(self):
        return await self.queue.get()

    async def close(self, reason):
        self.closed = True


@asynccontextmanager
async def local_transport_server(*, block_seconds: float = 0.0, baseline: bool = False, fatal_code: str = "", candidate_audio_enabled: bool = False, transport_options: dict | None = None):
    app = FastAPI()
    managers: list[SlowAudioManager] = []
    records = {}
    actor = SimpleNamespace(organization_id=uuid4(), actor_id=uuid4())
    repository = SimpleNamespace(
        get_session=lambda organization_id, actor_id, session_id: records.get(session_id),
        activate=lambda *args: True,
        pause=lambda *args: True,
        end=lambda *args, **kwargs: True,
    )

    async def build_manager(websocket, repository, record):
        manager = SlowAudioManager(block_seconds=block_seconds)
        manager.fatal_code = fatal_code
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
            status=LiveInterviewStatus.PREPARING,
            organization_id=actor.organization_id,
            actor_id=actor.actor_id,
            candidate_audio_enabled=candidate_audio_enabled,
        )
        return f"ws://127.0.0.1:{port}/api/career/live-interviews/{session_id}/stream"

    with (
        patch.object(live_web, "_resolve_websocket_actor", AsyncMock(return_value=actor)),
        patch.object(live_web, "_repository_for_websocket", return_value=repository),
        patch.object(live_web, "_build_live_manager", side_effect=build_manager),
        patch.object(live_web, "_run_live_transport", partial(live_web._run_live_transport, **(transport_options or {}))),
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


@pytest.mark.parametrize("old_limits", [True, False], ids=["old_20_frames_fails", "default_preserves_five_seconds"])
def test_five_second_dual_track_stall_with_extra_frame_preserves_audio_and_pings(old_limits):
    async def scenario():
        options = {"max_audio_frames": 20, "audio_send_timeout_seconds": 3} if old_limits else None
        async with local_transport_server(block_seconds=5, candidate_audio_enabled=True, transport_options=options) as (url, managers):
            async with connect(url(), ping_interval=None) as websocket:
                assert json.loads(await websocket.recv())["type"] == "session.ready"
                started = time.perf_counter()
                pings = []
                pongs_during_block = 0
                failure = None
                for sequence in range(70):
                    # 第0、1帧立即到达，多出100ms突发；随后仍持续每100ms输入。
                    await asyncio.sleep(max(0, started + max(0, sequence - 1) * .1 - time.perf_counter()))
                    for channel in AudioChannel:
                        payload = audio_payload(sequence)
                        payload["channel"] = channel.value
                        payload["pcm_base64"] = base64.b64encode(sequence.to_bytes(2, "little") * 2400).decode("ascii")
                        await websocket.send(json.dumps(payload))
                    if sequence % 2 == 1:
                        ping_at = time.perf_counter()
                        await websocket.send(json.dumps({"type": "ping"}))
                        reply = json.loads(await asyncio.wait_for(websocket.recv(), .5))
                        if reply["type"] == "error":
                            failure = reply
                            break
                        assert reply["type"] == "pong"
                        pings.append((time.perf_counter() - ping_at) * 1000)
                        pongs_during_block += time.perf_counter() - started < 4.5
                manager = managers[0]
                if old_limits:
                    assert failure and failure["code"] == "asr_audio_backpressure"
                    assert time.perf_counter() - started < 3.5
                    with pytest.raises(ConnectionClosed) as closed:
                        await websocket.recv()
                    assert closed.value.rcvd.code == 1013
                else:
                    assert failure is None
                    async with asyncio.timeout(2):
                        while any(len(manager.frames_by_channel[channel]) < 70 for channel in AudioChannel):
                            await asyncio.sleep(.01)
                    expected_hash = hashlib.sha256(b"".join(sequence.to_bytes(2, "little") * 2400 for sequence in range(70))).hexdigest()
                    for channel in AudioChannel:
                        assert manager.frames_by_channel[channel] == list(range(70))
                        assert manager.pcm_digest_by_channel[channel].hexdigest() == expected_hash
                    assert len(managers) == 1 and not manager.closed
                    assert manager.max_active_writes == 2
                    assert pongs_during_block >= 10
                    await websocket.send(json.dumps({"type": "session.end"}))
                print(json.dumps({"case": "old_limits" if old_limits else "pcm_budget", "upstream_stall_seconds": 5, "channels": 2, "received_frames": {channel.value: len(manager.frames_by_channel[channel]) for channel in AudioChannel}, "pongs": len(pings), "pong_p95_ms": round(sorted(pings)[max(0, int(len(pings) * .95) - 1)], 3), "explicit_failure": failure["code"] if failure else None, "same_connection": not old_limits}))
            await asyncio.sleep(.05)
            assert managers[0].closed and managers[0].active_writes == 0
    asyncio.run(scenario())


def test_pcm_budget_counts_inflight_bytes_instead_of_frame_count():
    async def scenario():
        entered = asyncio.Event()
        async def blocked(event):
            entered.set()
            await asyncio.Event().wait()
        relay = LiveAudioRelay(blocked, (AudioChannel.INTERVIEWER,), max_buffered_audio_seconds=1)
        try:
            relay.enqueue(AudioAppendEvent(AudioChannel.INTERVIEWER, 0, b"\0" * 38400))
            await entered.wait()
            relay.enqueue(AudioAppendEvent(AudioChannel.INTERVIEWER, 1, b"\0" * 9600))
            with pytest.raises(AudioRelayError) as failure:
                relay.enqueue(AudioAppendEvent(AudioChannel.INTERVIEWER, 2, b"\0\0"))
            assert failure.value.code == "asr_audio_budget_exceeded"
            assert failure.value.details["buffered_audio_bytes"] == 48000
            assert failure.value.details["audio_budget_bytes"] == 48000
        finally:
            await relay.close()
    asyncio.run(scenario())


def test_optional_channel_timeout_preserves_interviewer():
    async def scenario():
        notices = []
        writes = []

        async def write(event):
            if event.channel is AudioChannel.CANDIDATE:
                await asyncio.Event().wait()
            writes.append(event.sequence)

        async def disable(channel, message):
            notices.append(channel)

        relay = LiveAudioRelay(write, tuple(AudioChannel), send_timeout_seconds=.1, on_optional_failure=disable)
        try:
            relay.enqueue(AudioAppendEvent(AudioChannel.CANDIDATE, 0, b"\0\0"))
            relay.enqueue(AudioAppendEvent(AudioChannel.INTERVIEWER, 0, b"\0\0"))
            async with asyncio.timeout(1):
                while not notices:
                    await asyncio.sleep(.01)
            relay.enqueue(AudioAppendEvent(AudioChannel.CANDIDATE, 1, b"\0\0"))
            relay.enqueue(AudioAppendEvent(AudioChannel.INTERVIEWER, 1, b"\0\0"))
            await relay.drain()
            assert notices == [AudioChannel.CANDIDATE]
            assert writes == [0, 1]
            assert not any(relay.buffered_audio_bytes.values())
        finally:
            await relay.close()
    asyncio.run(scenario())


def test_optional_track_end_follows_its_last_audio_and_is_idempotent():
    async def scenario():
        async with local_transport_server(candidate_audio_enabled=True) as (url, managers):
            async with connect(url()) as ws:
                assert json.loads(await ws.recv())["type"] == "session.ready"
                frame = audio_payload(0)
                frame["channel"] = "candidate"
                await ws.send(json.dumps(frame))
                ending = json.dumps({"type": "audio.channel_end", "channel": "candidate"})
                await ws.send(ending)
                await ws.send(ending)
                notice = json.loads(await asyncio.wait_for(ws.recv(), 2))
                assert notice["type"] == "channel.ended" and notice["channel"] == "candidate"
                assert managers[0].frames_by_channel[AudioChannel.CANDIDATE] == [0]
                await ws.send(json.dumps(audio_payload(1)))
                await ws.send(json.dumps({"type": "session.end"}))
                assert json.loads(await asyncio.wait_for(ws.recv(), 2))["type"] == "session.ended"
                assert managers[0].frames_by_channel[AudioChannel.INTERVIEWER] == [1]
    asyncio.run(scenario())


def test_session_end_drains_pending_frames_before_ack_and_clean_close():
    async def scenario():
        async with local_transport_server(block_seconds=.15) as (url, managers):
            async with connect(url()) as ws:
                assert json.loads(await ws.recv())["type"] == "session.ready"
                for index in range(4):
                    await ws.send(json.dumps(audio_payload(index)))
                await ws.send(json.dumps({"type": "session.end"}))
                assert json.loads(await asyncio.wait_for(ws.recv(), 2))["type"] == "session.ended"
                assert managers[0].frames == [0, 1, 2, 3]
                with pytest.raises(ConnectionClosed) as closed:
                    await ws.recv()
                assert closed.value.rcvd.code == 1000
    asyncio.run(scenario())


def test_small_pcm_frames_do_not_hit_arbitrary_twenty_frame_limit():
    async def scenario():
        received = []
        async def consume(event):
            received.append(event.sequence)
        relay = LiveAudioRelay(consume, (AudioChannel.INTERVIEWER,))
        try:
            for sequence in range(100):
                relay.enqueue(AudioAppendEvent(AudioChannel.INTERVIEWER, sequence, b"\0\0"))
            await asyncio.wait_for(relay._queues[AudioChannel.INTERVIEWER].join(), 1)
            assert received == list(range(100))
        finally:
            await relay.close()
    asyncio.run(scenario())


def test_empty_pcm_and_commit_events_cannot_bypass_finite_budget():
    async def scenario():
        async def blocked(event):
            await asyncio.Event().wait()
        relay = LiveAudioRelay(blocked, (AudioChannel.INTERVIEWER,))
        try:
            for sequence in range(100):
                with pytest.raises(ValueError):
                    relay.enqueue(AudioAppendEvent(AudioChannel.INTERVIEWER, sequence, b""))
            assert relay.buffered_audio_bytes[AudioChannel.INTERVIEWER] == 0
            with pytest.raises(AudioRelayError) as failure:
                for _ in range(1000):
                    relay.enqueue(AudioCommitEvent(AudioChannel.INTERVIEWER))
            assert failure.value.code == "asr_control_budget_exceeded"
            assert relay._queues[AudioChannel.INTERVIEWER].qsize() <= 64
        finally:
            await relay.close()
    asyncio.run(scenario())


def test_successful_writer_releases_budget_over_one_hour_equivalent_audio():
    async def scenario():
        delivered = 0
        async def consume(event):
            nonlocal delivered
            delivered += len(event.pcm)
        relay = LiveAudioRelay(consume, (AudioChannel.INTERVIEWER,), max_buffered_audio_seconds=2)
        packet = b"\0" * 96000  # 每包2秒，1800包是一小时PCM；加速核对记账而非时长压测。
        try:
            for sequence in range(1800):
                relay.enqueue(AudioAppendEvent(AudioChannel.INTERVIEWER, sequence, packet))
                await asyncio.wait_for(relay._queues[AudioChannel.INTERVIEWER].join(), 1)
                assert relay.buffered_audio_bytes[AudioChannel.INTERVIEWER] == 0
            assert delivered == 3600 * 48000
        finally:
            await relay.close()
    asyncio.run(scenario())


def test_asr_audio_timeout_closes_with_structured_error_and_releases_writer():
    async def scenario():
        async with local_transport_server(block_seconds=20, transport_options={"audio_send_timeout_seconds": .3}) as (url, managers):
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
        async with local_transport_server(block_seconds=20, transport_options={"max_audio_frames": 20}) as (url, managers):
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


def test_default_pcm_budget_exceeded_is_reported_as_exceeded_not_short_stall():
    async def scenario():
        async with local_transport_server(block_seconds=20) as (url, managers):
            async with connect(url(), ping_interval=None) as websocket:
                await websocket.recv()
                for sequence in range(4):
                    payload = audio_payload(sequence)
                    payload["pcm_base64"] = base64.b64encode(b"\0" * 96000).decode("ascii")
                    await websocket.send(json.dumps(payload))
                error = json.loads(await asyncio.wait_for(websocket.recv(), 1))
                assert error["code"] == "asr_audio_budget_exceeded"
                assert "6 秒缓冲预算" in error["message"]
                assert error["fatal"] is True
                with pytest.raises(ConnectionClosed) as closed:
                    await websocket.recv()
                assert closed.value.rcvd.code == 1013
            await asyncio.sleep(.05)
            assert managers[0].closed and managers[0].active_writes == 0
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


@pytest.mark.parametrize("code", ["asr_stream_ended", "asr_stream_failed"])
def test_fatal_asr_error_is_flushed_before_real_websocket_closes(code):
    async def scenario():
        async with local_transport_server(fatal_code=code) as (url, managers):
            async with connect(url(), ping_interval=None) as websocket:
                assert json.loads(await websocket.recv())["type"] == "session.ready"
                event = json.loads(await websocket.recv())
                assert event == {"type": "error", "code": code, "fatal": True}
                with pytest.raises(ConnectionClosed) as closed:
                    await websocket.recv()
                assert closed.value.rcvd.code == 1013
            await asyncio.sleep(0.05)
            assert managers[0].closed

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
