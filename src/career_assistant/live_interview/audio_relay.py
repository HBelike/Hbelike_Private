"""隔离浏览器收包与 ASR 发送，避免上游背压阻塞 WebSocket 心跳。"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable

from src.career_assistant.live_interview.contracts import (
    AudioAppendEvent,
    AudioChannel,
    AudioCommitEvent,
)


AudioEvent = AudioAppendEvent | AudioCommitEvent
PCM_BYTES_PER_SECOND = 24_000 * 2
DEFAULT_AUDIO_BUFFER_SECONDS = 6.0
DEFAULT_AUDIO_QUEUE_FRAMES: int | None = None
DEFAULT_AUDIO_SEND_TIMEOUT_SECONDS = 6.0
MAX_PENDING_CONTROL_EVENTS = 64


class AudioRelayError(RuntimeError):
    """只暴露稳定错误码与可操作提示，不转发 Provider 内部信息。"""

    def __init__(self, code: str, message: str, *, details: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.details = details or {}


class LiveAudioRelay:
    """每轨按待发送 PCM（含发送中）限额保序；超预算明确失败，不静默丢帧。"""

    def __init__(
        self,
        handler: Callable[[AudioEvent], Awaitable[None]],
        channels: tuple[AudioChannel, ...],
        *,
        max_frames: int | None = DEFAULT_AUDIO_QUEUE_FRAMES,
        max_buffered_audio_seconds: float = DEFAULT_AUDIO_BUFFER_SECONDS,
        send_timeout_seconds: float = DEFAULT_AUDIO_SEND_TIMEOUT_SECONDS,
    ) -> None:
        if max_frames is not None and max_frames < 1:
            raise ValueError("显式音频帧上限必须大于 0")
        if not math.isfinite(max_buffered_audio_seconds) or max_buffered_audio_seconds <= 0 or not math.isfinite(send_timeout_seconds) or send_timeout_seconds <= 0:
            raise ValueError("音频时长预算与发送超时必须是有限正数")
        self._handler = handler
        self._send_timeout = send_timeout_seconds
        self._audio_budget_bytes = int(max_buffered_audio_seconds * PCM_BYTES_PER_SECOND)
        if self._audio_budget_bytes < 1:
            raise ValueError("音频预算至少需要容纳一个字节")
        self._max_frames = max_frames
        self._pending_audio_bytes = {channel: 0 for channel in channels}
        self._pending_control_events = {channel: 0 for channel in channels}
        self._queues: dict[AudioChannel, asyncio.Queue[AudioEvent]] = {
            channel: asyncio.Queue(maxsize=max_frames or 0) for channel in channels
        }
        self._failed = asyncio.Event()
        self._failure: AudioRelayError | None = None
        self._closed = False
        self._writers = [
            asyncio.create_task(self._write(channel, queue), name=f"live-audio-{channel.value}")
            for channel, queue in self._queues.items()
        ]

    @property
    def buffered_audio_bytes(self) -> dict[AudioChannel, int]:
        """尚未写完的 PCM 字节快照；不是 ASR 识别确认水位。"""
        return dict(self._pending_audio_bytes)

    def enqueue(self, event: AudioEvent) -> None:
        if self._failure is not None:
            raise self._failure
        if self._closed:
            raise RuntimeError("音频转发已关闭")
        queue = self._queues.get(event.channel)
        if queue is None:
            raise ValueError(f"当前会话未启用 {event.channel.value} 音频轨道")
        pcm_bytes = len(event.pcm) if isinstance(event, AudioAppendEvent) else 0
        if isinstance(event, AudioAppendEvent) and pcm_bytes == 0:
            raise ValueError("PCM 音频帧不能为空")
        details: dict[str, object] = {
            "channel": event.channel.value,
            "buffered_audio_bytes": self._pending_audio_bytes[event.channel],
            "audio_budget_bytes": self._audio_budget_bytes,
            "incoming_audio_bytes": pcm_bytes,
        }
        if self._pending_audio_bytes[event.channel] + pcm_bytes > self._audio_budget_bytes:
            seconds = self._audio_budget_bytes / PCM_BYTES_PER_SECOND
            error = AudioRelayError(
                "asr_audio_budget_exceeded",
                f"语音积压已超过 {seconds:g} 秒缓冲预算，采集已暂停，请重新连接后继续。",
                details=details,
            )
            self._fail(error)
            raise error
        if isinstance(event, AudioCommitEvent) and self._pending_control_events[event.channel] >= MAX_PENDING_CONTROL_EVENTS:
            error = AudioRelayError(
                "asr_control_budget_exceeded", "音频控制请求积压已超过上限，采集已暂停。", details=details,
            )
            self._fail(error)
            raise error
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull as exc:
            error = AudioRelayError(
                "asr_audio_backpressure",
                "音频队列已达到显式帧数上限，采集已暂停，请重新连接后继续。",
                details={**details, "max_frames": self._max_frames},
            )
            self._fail(error)
            raise error from exc
        self._pending_audio_bytes[event.channel] += pcm_bytes
        if isinstance(event, AudioCommitEvent):
            self._pending_control_events[event.channel] += 1

    def _fail(self, error: AudioRelayError) -> None:
        if self._failure is None:
            self._failure = error
            self._failed.set()

    async def _write(self, channel: AudioChannel, queue: asyncio.Queue[AudioEvent]) -> None:
        try:
            while True:
                event = await queue.get()
                try:
                    await asyncio.wait_for(self._handler(event), timeout=self._send_timeout)
                finally:
                    # 从队列取出不代表已发送；完成/取消当前写入后才释放 in-flight 额度。
                    if isinstance(event, AudioAppendEvent):
                        self._pending_audio_bytes[channel] -= len(event.pcm)
                    else:
                        self._pending_control_events[channel] -= 1
                    queue.task_done()
                    del event
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            self._fail(AudioRelayError(
                "asr_audio_timeout",
                f"语音发送已超过 {self._send_timeout:g} 秒等待预算，采集已暂停，请重新连接后继续。",
                details={"channel": channel.value, "send_timeout_seconds": self._send_timeout},
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
                queue.task_done()
        for channel in self._queues:
            self._pending_audio_bytes[channel] = 0
            self._pending_control_events[channel] = 0
