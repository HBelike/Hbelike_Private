"""验证真实 Provider 协议中的编号、错误事件与收尾。"""

import asyncio
import json
from unittest.mock import AsyncMock, patch

import pytest

from src.career_assistant.live_interview.asr.dashscope_realtime import DashScopeRealtimeAsrSession
from src.career_assistant.live_interview.asr.openai_realtime import OpenAIRealtimeAsrProvider, OpenAIRealtimeAsrSession
from src.career_assistant.live_interview.contracts import AudioChannel


class ScriptedSocket:
    def __init__(self, *messages):
        self.messages = [json.dumps(message) for message in messages]
        self.sent = []
        self.closed = False

    async def send(self, value):
        self.sent.append(json.loads(value) if isinstance(value, str) else value)

    async def recv(self):
        return self.messages.pop(0)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.messages:
            raise StopAsyncIteration
        return await self.recv()

    async def close(self):
        self.closed = True


def dashscope_result(sentence_id, value):
    sentence = {"text": value, "sentence_end": True}
    if sentence_id is not None:
        sentence["sentence_id"] = sentence_id
    return {"header": {"event": "result-generated"}, "payload": {"output": {"sentence": sentence}}}


def test_dashscope_results_keep_sentence_ids_and_read_tail_after_finish():
    async def verify():
        socket = ScriptedSocket(dashscope_result(0, "问题一？"), dashscope_result(1, "问题二？"),
                                dashscope_result(1, "问题二？"), dashscope_result(None, "尾句"),
                                {"header": {"event": "task-finished"}})
        session = DashScopeRealtimeAsrSession(socket, AudioChannel.INTERVIEWER, "test")
        await session.append_audio(b"\x00\x01", 800)
        await session.finish()
        assert not socket.closed
        assert [(event.sequence, event.text) async for event in session.events()] == [
            (0, "问题一？"), (1, "问题二？"), (1, "问题二？"), (2, "尾句"),
        ]
        await session.close()
        assert socket.closed
        assert sum(isinstance(msg, dict) and msg["header"]["action"] == "finish-task" for msg in socket.sent) == 1
    asyncio.run(verify())


def test_finish_requires_provider_confirmation():
    async def verify():
        session = DashScopeRealtimeAsrSession(ScriptedSocket(), AudioChannel.INTERVIEWER, "test")
        await session.finish()
        with pytest.raises(RuntimeError, match="task-finished"):
            _ = [event async for event in session.events()]
        await session.close()
    asyncio.run(verify())


def test_openai_uses_transcription_session_and_accumulates_partial_by_item():
    async def verify():
        prefix = "conversation.item.input_audio_transcription."
        socket = ScriptedSocket(
            {"type": "session.created"}, {"type": "session.updated"},
            {"type": "input_audio_buffer.committed", "item_id": "a"},
            {"type": prefix + "delta", "item_id": "a", "delta": "one"},
            {"type": prefix + "delta", "item_id": "a", "delta": " two"},
            {"type": "input_audio_buffer.committed", "item_id": "b"},
            {"type": prefix + "completed", "item_id": "b", "transcript": "three"},
            {"type": prefix + "completed", "item_id": "a", "transcript": "one two"},
        )
        with patch("websockets.asyncio.client.connect", AsyncMock(return_value=socket)) as connect:
            session = await OpenAIRealtimeAsrProvider("local-only").start(AudioChannel.INTERVIEWER)
        assert connect.await_args.args[0].endswith("?intent=transcription")
        assert socket.sent[0]["session"]["audio"]["input"]["transcription"]["model"] == "gpt-4o-transcribe"
        await session.append_audio(bytes(4800), 120)
        assert [(event.sequence, event.text, event.is_final) async for event in session.events()] == [
            (1, "one", False), (1, "one two", False), (2, "three", True), (1, "one two", True),
        ]
        await session.close()
    asyncio.run(verify())


def test_openai_configuration_error_closes_failed_socket():
    async def verify():
        socket = ScriptedSocket({"type": "error"})
        with patch("websockets.asyncio.client.connect", AsyncMock(return_value=socket)):
            with pytest.raises(RuntimeError, match="配置"):
                await OpenAIRealtimeAsrProvider("local-only").start(AudioChannel.INTERVIEWER)
        assert socket.closed
    asyncio.run(verify())


def test_openai_finish_waits_for_manual_commit_and_its_last_transcription():
    async def verify():
        def completed(item, value):
            return {"type": "conversation.item.input_audio_transcription.completed", "item_id": item, "transcript": value}

        socket = ScriptedSocket(
            {"type": "input_audio_buffer.committed", "item_id": "vad"}, completed("vad", "上一句"),
            {"type": "input_audio_buffer.committed", "item_id": "tail"},
            {"type": "session.updated"}, completed("tail", "最后一句"),
        )
        session = OpenAIRealtimeAsrSession(socket, AudioChannel.INTERVIEWER)
        await session.append_audio(bytes(4800), 2)
        await session.finish()
        assert [message["type"] for message in socket.sent[-2:]] == ["input_audio_buffer.commit", "session.update"]
        assert [event.text async for event in session.events()] == ["上一句", "最后一句"]
        await session.close()
    asyncio.run(verify())


def test_openai_finish_reports_missing_confirmation():
    async def verify():
        socket = ScriptedSocket({"type": "error", "error": {"code": "input_audio_buffer_commit_empty"}})
        session = OpenAIRealtimeAsrSession(socket, AudioChannel.INTERVIEWER)
        await session.append_audio(bytes(4800), 2)
        await session.finish()
        with pytest.raises(RuntimeError, match="尾句确认"):
            _ = [event async for event in session.events()]
        await session.close()
    asyncio.run(verify())

