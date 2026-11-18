"""隔离浏览器收包与 ASR 发送，避免上游背压阻塞 WebSocket 心跳。"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from src.career_assistant.live_interview.contracts import (
    AudioAppendEvent,
    AudioChannel,
    AudioCommitEvent,
)


AudioEvent = AudioAppendEvent | AudioCommitEvent
DEFAULT_AUDIO_QUEUE_FRAMES = 20
DEFAULT_AUDIO_SEND_TIMEOUT_SECONDS = 3.0


class AudioRelayError(RuntimeError):
    """只暴露稳定错误码与可操作提示，不转发 Provider 内部信息。"""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class LiveAudioRelay:
    """每条音轨一个有界 FIFO；满队列或上游卡住时明确失败，不静默丢帧。"""

    def __init__(
        self,
        handler: Callable[[AudioEvent], Awaitable[None]],
        channels: tuple[AudioChannel, ...],
        *,
        max_frames: int = DEFAULT_AUDIO_QUEUE_FRAMES,
        send_timeout_seconds: float = DEFAULT_AUDIO_SEND_TIMEOUT_SECONDS,
    ) -> None:
        if max_frames < 1 or send_timeout_seconds <= 0:
            raise ValueError("音频队列与发送超时必须大于 0")
        self._handler = handler
        self._send_timeout = send_timeout_seconds
        self._queues: dict[AudioChannel, asyncio.Queue[AudioEvent]] = {
            channel: asyncio.Queue(maxsize=max_frames) for channel in channels
        }
        self._failed = asyncio.Event()
        self._failure: AudioRelayError | None = None
        self._closed = False
        self._writers = [
            asyncio.create_task(self._write(queue), name=f"live-audio-{channel.value}")
            for channel, queue in self._queues.items()
        ]

    def enqueue(self, event: AudioEvent) -> None:
        if self._failure is not None:
            raise self._failure
        if self._closed:
            raise RuntimeError("音频转发已关闭")
        queue = self._queues.get(event.channel)
        if queue is None:
            raise ValueError(f"当前会话未启用 {event.channel.value} 音频轨道")
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull as exc:
            error = AudioRelayError(
                "asr_audio_backpressure",
                "语音识别服务接收音频过慢，采集已暂停，请重新连接后继续。",
            )
            self._fail(error)
            raise error from exc

    def _fail(self, error: AudioRelayError) -> None:
        if self._failure is None:
            self._failure = error
            self._failed.set()

    async def _write(self, queue: asyncio.Queue[AudioEvent]) -> None:
        try:
            while True:
                event = await queue.get()
                await asyncio.wait_for(self._handler(event), timeout=self._send_timeout)
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            self._fail(AudioRelayError(
                "asr_audio_timeout",
                "语音识别服务接收音频超时，采集已暂停，请重新连接后继续。",
            ))
        except Exception:
            self._fail(AudioRelayError(
                "asr_audio_failed",
                "语音识别连接已中断，采集已暂停，请重新连接后继续。",
            ))

    async def wait_failed(self) -> None:
        await self._failed.wait()
        assert self._failure is not None
        raise self._failure

    async def close(self) -> None:
        self._closed = True
        for task in self._writers:
            task.cancel()
        await asyncio.gather(*self._writers, return_exceptions=True)
        for queue in self._queues.values():
            while not queue.empty():
                queue.get_nowait()
