"""OpenAI Realtime transcription 服务端 WebSocket 适配器。"""

from __future__ import annotations

import asyncio
import base64
import json
from collections.abc import AsyncIterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.career_assistant.live_interview.contracts import AudioChannel, TranscriptEvent


def map_openai_event(
    payload: dict[str, object],
    channel: AudioChannel,
    sequence: int,
) -> TranscriptEvent | None:
    event_type = str(payload.get("type", ""))
    if event_type == "conversation.item.input_audio_transcription.delta":
        text = str(payload.get("delta", "")).strip()
        return TranscriptEvent(channel, sequence, text, False, provider="openai") if text else None
    if event_type == "conversation.item.input_audio_transcription.completed":
        text = str(payload.get("transcript", "")).strip()
        return TranscriptEvent(channel, sequence, text, True, provider="openai") if text else None
    return None


class OpenAIRealtimeAsrSession:
    def __init__(self, websocket: object, channel: AudioChannel) -> None:
        self._websocket = websocket
        self._channel = channel
        self._next_sentence = 1
        self._items: dict[str, int] = {}
        self._partials: dict[str, str] = {}
        self._pending: set[str] = set()
        self._has_audio = False
        self._finishing = False
        self._finish_confirmed = False
        self._closed = False

    async def append_audio(self, pcm: bytes, sequence: int) -> None:
        if self._closed or self._finishing:
            raise RuntimeError("OpenAI 转写已停止接收音频")
        self._has_audio = True
        await self._websocket.send(
            json.dumps(
                {
                    "type": "input_audio_buffer.append",
                    "audio": base64.b64encode(pcm).decode("ascii"),
                }
            )
        )

    async def commit(self) -> None:
        await self._websocket.send(json.dumps({"type": "input_audio_buffer.commit"}))

    async def events(self) -> AsyncIterator[TranscriptEvent]:
        async for message in self._websocket:
            payload = json.loads(message)
            kind = payload.get("type", "")
            item_id = payload.get("item_id")
            if kind == "error":
                code = (payload.get("error") or {}).get("code")
                if not (self._finishing and code == "input_audio_buffer_commit_empty"):
                    raise RuntimeError("OpenAI 实时转写任务失败")
            elif kind in {"session.updated", "transcription_session.updated"} and self._finishing:
                self._finish_confirmed = True
            elif kind == "conversation.item.input_audio_transcription.failed":
                raise RuntimeError("OpenAI 语句转写失败")
            elif isinstance(item_id, str) and item_id:
                if item_id not in self._items:
                    self._items[item_id] = self._next_sentence
                    self._next_sentence += 1
                if kind == "input_audio_buffer.committed":
                    # VAD 的确认可能比下一句音频迟到。收尾仍提交剩余缓冲；
                    # 已由 VAD 完整提交时，服务端会返回可辨认的空缓冲事件。
                    self._pending.add(item_id)
                elif kind == "conversation.item.input_audio_transcription.delta":
                    partial = self._partials.get(item_id, "") + str(payload.get("delta") or "")
                    self._partials[item_id] = partial
                    if partial.strip():
                        yield TranscriptEvent(self._channel, self._items[item_id], partial, False, provider="openai")
                elif kind == "conversation.item.input_audio_transcription.completed":
                    self._partials.pop(item_id, None)
                    self._pending.discard(item_id)
                    event = map_openai_event(payload, self._channel, self._items[item_id])
                    if event is not None:
                        yield event
            if self._finish_confirmed and not self._pending:
                return
        if self._finishing and not self._closed:
            raise RuntimeError("OpenAI 转写连接在尾句确认前关闭")

    async def finish(self) -> None:
        if self._finishing or self._closed:
            return
        self._finishing = True
        if self._has_audio:
            await self.commit()
            # committed 不带对应的客户端请求 ID，旧 VAD 的确认也可能迟到。
            # 在最后的 commit 之后按序请求配置回执，等已提交的所有 item 转写完成。
            await self._websocket.send(json.dumps({"type": "session.update", "session": {"type": "transcription"}}))
        elif not self._pending:
            await self.close()

    async def close(self) -> None:
        if not self._closed:
            self._closed = True
            await self._websocket.close()


class OpenAIRealtimeAsrProvider:
    sample_rate = 24_000

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "gpt-4o-transcribe",
        base_url: str = "wss://api.openai.com/v1/realtime",
    ) -> None:
        self._api_key = api_key.strip()
        self._model = model.strip()
        self._base_url = base_url.rstrip("/")
        if not self._api_key:
            raise ValueError("OpenAI ASR API Key 不能为空")

    async def start(
        self,
        channel: AudioChannel,
        *,
        language_hint: str | None = None,
        prompt: str = "",
    ) -> OpenAIRealtimeAsrSession:
        from websockets.asyncio.client import connect

        parts = urlsplit(self._base_url)
        query = dict(parse_qsl(parts.query))
        query.pop("model", None)
        query["intent"] = "transcription"
        websocket = await connect(
            urlunsplit(parts._replace(query=urlencode(query))),
            additional_headers={"Authorization": f"Bearer {self._api_key}"},
            max_size=2**20,
        )
        transcription: dict[str, object] = {"model": self._model, "prompt": prompt[:2_000]}
        if language_hint in {"zh", "en"}:
            transcription["language"] = language_hint
        update = {
            "type": "session.update",
            "session": {
                "type": "transcription",
                "audio": {"input": {
                    "format": {"type": "audio/pcm", "rate": self.sample_rate},
                    "transcription": transcription,
                    "turn_detection": {"type": "server_vad"},
                }},
            },
        }
        try:
            async with asyncio.timeout(15):
                await websocket.send(json.dumps(update))
                while True:
                    received = json.loads(await websocket.recv())
                    if received.get("type") in {"session.updated", "transcription_session.updated"}:
                        break
                    if received.get("type") == "error":
                        raise RuntimeError("OpenAI 实时转写配置未获确认")
        except BaseException:
            await websocket.close()
            raise
        return OpenAIRealtimeAsrSession(websocket, channel)
