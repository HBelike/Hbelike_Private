"""阿里云百炼 Qwen-Audio 实时语音识别 WebSocket 适配器。"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import suppress
from uuid import uuid4

from src.career_assistant.live_interview.contracts import AudioChannel, TranscriptEvent


DEFAULT_DASHSCOPE_ASR_MODEL = "qwen-audio-3.1-asr-flash-streaming"
DEFAULT_DASHSCOPE_ASR_URL = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
DEFAULT_DASHSCOPE_MAX_SENTENCE_SILENCE_MS = 500
DEFAULT_DASHSCOPE_VAD_MODEL = "near_meeting_16k"


def map_dashscope_event(
    payload: dict[str, object],
    channel: AudioChannel,
    sequence: int,
) -> TranscriptEvent | None:
    """把 DashScope result-generated 事件转换为稳定领域事件。"""

    header = payload.get("header")
    if not isinstance(header, dict) or header.get("event") != "result-generated":
        return None
    event_payload = payload.get("payload")
    if not isinstance(event_payload, dict):
        return None
    output = event_payload.get("output")
    if not isinstance(output, dict):
        return None
    sentence = output.get("sentence")
    if not isinstance(sentence, dict):
        return None
    text = str(sentence.get("text") or "").strip()
    if not text or sentence.get("heartbeat"):
        return None
    sentence_id = sentence.get("sentence_id")
    if type(sentence_id) is int and sentence_id >= 0:
        sequence = sentence_id
    return TranscriptEvent(
        channel=channel,
        sequence=sequence,
        text=text,
        is_final=bool(sentence.get("sentence_end", False)),
        provider="dashscope",
    )


class DashScopeRealtimeAsrSession:
    def __init__(self, websocket: object, channel: AudioChannel, task_id: str) -> None:
        self._websocket = websocket
        self._channel = channel
        self._task_id = task_id
        self._next_sentence = 1
        self._finishing = False
        self._closed = False

    async def append_audio(self, pcm: bytes, sequence: int) -> None:
        if self._closed or self._finishing:
            raise RuntimeError("DashScope ASR 会话已关闭")
        await self._websocket.send(pcm)

    async def commit(self) -> None:
        # Qwen-Audio Streaming 使用服务端 VAD 自动输出 sentence_end，无需逐句提交。
        return None

    async def events(self) -> AsyncIterator[TranscriptEvent]:
        async for message in self._websocket:
            if not isinstance(message, str):
                continue
            payload = json.loads(message)
            if not isinstance(payload, dict):
                continue
            header = payload.get("header")
            event_type = header.get("event") if isinstance(header, dict) else None
            if event_type == "task-failed":
                raise RuntimeError("DashScope 实时转写任务失败")
            if event_type == "task-finished":
                return
            event = map_dashscope_event(payload, self._channel, self._next_sentence)
            if event is not None:
                if event.is_final:
                    self._next_sentence = max(self._next_sentence, event.sequence + 1)
                yield event
        if self._finishing and not self._closed:
            raise RuntimeError("语音服务未返回 task-finished")

    async def finish(self) -> None:
        if self._closed or self._finishing:
            return
        self._finishing = True
        await self._websocket.send(json.dumps({
            "header": {"action": "finish-task", "task_id": self._task_id, "streaming": "duplex"},
            "payload": {"input": {}},
        }))

    async def close(self) -> None:
        if self._closed:
            return
        with suppress(Exception):
            async with asyncio.timeout(2):
                await self.finish()
        self._closed = True
        await self._websocket.close()


class DashScopeRealtimeAsrProvider:
    sample_rate = 24_000

    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_DASHSCOPE_ASR_MODEL,
        base_url: str = DEFAULT_DASHSCOPE_ASR_URL,
        workspace_id: str = "",
        max_sentence_silence_ms: int = DEFAULT_DASHSCOPE_MAX_SENTENCE_SILENCE_MS,
        vad_model: str = DEFAULT_DASHSCOPE_VAD_MODEL,
    ) -> None:
        self._api_key = api_key.strip()
        self._model = model.strip() or DEFAULT_DASHSCOPE_ASR_MODEL
        self._base_url = base_url.strip() or DEFAULT_DASHSCOPE_ASR_URL
        self._workspace_id = workspace_id.strip()
        self._max_sentence_silence_ms = max_sentence_silence_ms
        self._vad_model = vad_model.strip()
        if not self._api_key:
            raise ValueError("DashScope ASR API Key 不能为空")
        if not 200 <= self._max_sentence_silence_ms <= 6_000:
            raise ValueError("DashScope 断句静音阈值必须在 200 到 6000 毫秒之间")
        if self._vad_model not in {"near_meeting_16k", "far_field_meeting_16k", ""}:
            raise ValueError("DashScope VAD 模型配置无效")

    async def start(
        self,
        channel: AudioChannel,
        *,
        language_hint: str | None = None,
        prompt: str = "",
    ) -> DashScopeRealtimeAsrSession:
        from websockets.asyncio.client import connect

        headers = {"Authorization": f"Bearer {self._api_key}"}
        if self._workspace_id:
            headers["X-DashScope-WorkSpace"] = self._workspace_id
        websocket = await connect(
            self._base_url,
            additional_headers=headers,
            max_size=2**20,
            ping_interval=20,
            ping_timeout=20,
        )
        task_id = str(uuid4())
        parameters: dict[str, object] = {
            "format": "pcm",
            "sample_rate": self.sample_rate,
            "heartbeat": True,
            "max_sentence_silence": self._max_sentence_silence_ms,
        }
        if self._model.startswith("qwen-audio-3.1-") and self._vad_model:
            parameters["vad_model"] = self._vad_model
        parameters["language_hints"] = (
            [language_hint] if language_hint in {"zh", "en"} else ["zh", "en"]
        )
        input_payload: dict[str, object] = {}
        if prompt.strip():
            input_payload["context"] = [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": prompt.strip()[:2_000]}],
                }
            ]
        task = {
            "header": {"action": "run-task", "task_id": task_id, "streaming": "duplex"},
            "payload": {
                "task_group": "audio", "task": "asr", "function": "recognition",
                "model": self._model, "parameters": parameters, "input": input_payload,
            },
        }
        try:
            async with asyncio.timeout(15):
                await websocket.send(json.dumps(task))
                while True:
                    message = await websocket.recv()
                    if not isinstance(message, str):
                        continue
                    payload = json.loads(message)
                    header = payload.get("header") if isinstance(payload, dict) else None
                    event_type = header.get("event") if isinstance(header, dict) else None
                    if event_type == "task-started":
                        break
                    if event_type == "task-failed":
                        raise RuntimeError("DashScope 实时转写任务启动失败")
        except BaseException:
            await websocket.close()
            raise
        return DashScopeRealtimeAsrSession(websocket, channel, task_id)
