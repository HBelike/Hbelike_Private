"""隔离浏览器收包与 ASR 发送，避免上游背压阻塞 WebSocket 心跳。"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable

from src.career_assistant.live_interview.contracts import (
    AudioAppendEvent,
    AudioChannel,
    AudioChannelEndEvent,
    AudioCommitEvent,
)


AudioEvent = AudioAppendEvent | AudioCommitEvent | AudioChannelEndEvent
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
        on_optional_failure: Callable[[AudioChannel, str], Awaitable[None]] | None = None,
    ) -> None:
        if max_frames is not None and max_frames < 1:
            raise ValueError("显式音频帧上限必须大于 0")
        if not math.isfinite(max_buffered_audio_seconds) or max_buffered_audio_seconds <= 0 or not math.isfinite(send_timeout_seconds) or send_timeout_seconds <= 0:
            raise ValueError("音频时长预算与发送超时必须是有限正数")
        self._handler = handler
        self._on_optional_failure = on_optional_failure
        self._optional_notice: asyncio.Task[None] | None = None
        self._finished_channels: set[AudioChannel] = set()
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
        self._writers = {
            channel: asyncio.create_task(self._write(channel, queue), name=f"live-audio-{channel.value}")
            for channel, queue in self._queues.items()
        }

    @property
    def buffered_audio_bytes(self) -> dict[AudioChannel, int]:
        """尚未写完的 PCM 字节快照；不是 ASR 识别确认水位。"""
        return dict(self._pending_audio_bytes)

    def enqueue(self, event: AudioEvent) -> None:
        if self._failure is not None:
            raise self._failure
        if self._closed:
            raise RuntimeError("音频转发已关闭")
        if event.channel in self._finished_channels:
            return
        queue = self._queues.get(event.channel)
        if queue is None:
            if isinstance(event, AudioChannelEndEvent):
                return
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
            self._fail_channel(event.channel, error)
            return
        if not isinstance(event, AudioAppendEvent) and self._pending_control_events[event.channel] >= MAX_PENDING_CONTROL_EVENTS:
            error = AudioRelayError(
                "asr_control_budget_exceeded", "音频控制请求积压已超过上限，采集已暂停。", details=details,
            )
            self._fail_channel(event.channel, error)
            return
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            error = AudioRelayError(
                "asr_audio_backpressure",
                "音频队列已达到显式帧数上限，采集已暂停，请重新连接后继续。",
                details={**details, "max_frames": self._max_frames},
            )
            self._fail_channel(event.channel, error)
            return
        self._pending_audio_bytes[event.channel] += pcm_bytes
        if not isinstance(event, AudioAppendEvent):
            self._pending_control_events[event.channel] += 1
        if isinstance(event, AudioChannelEndEvent):
            self._finished_channels.add(event.channel)

    def _fail_channel(self, channel: AudioChannel, error: AudioRelayError, *, writer: bool = False) -> None:
        if channel is AudioChannel.CANDIDATE and self._on_optional_failure is not None:
            self._finished_channels.add(channel)
            if not writer:
                self._writers[channel].cancel()
            self._discard_queued(channel)
            if self._optional_notice is None:
                self._optional_notice = asyncio.create_task(self._on_optional_failure(channel, str(error)))
            return
        self._fail(error)
        if not writer:
            raise error

    def _discard_queued(self, channel: AudioChannel) -> None:
        queue = self._queues[channel]
        while not queue.empty():
            event = queue.get_nowait()
            self._release_budget(channel, event)
            queue.task_done()

    def _release_budget(self, channel: AudioChannel, event: AudioEvent) -> None:
        if isinstance(event, AudioAppendEvent):
            self._pending_audio_bytes[channel] -= len(event.pcm)
        else:
            self._pending_control_events[channel] -= 1

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
                    self._release_budget(channel, event)
                    queue.task_done()
                    del event
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            self._fail_channel(channel, AudioRelayError(
                "asr_audio_timeout",
                f"语音发送已超过 {self._send_timeout:g} 秒等待预算，采集已暂停，请重新连接后继续。",
                details={"channel": channel.value, "send_timeout_seconds": self._send_timeout},
            ), writer=True)
        except Exception:
            self._fail_channel(channel, AudioRelayError(
                "asr_audio_failed",
                "语音识别连接已中断，采集已暂停，请重新连接后继续。",
            ), writer=True)

    async def drain(self) -> None:
        """先送完已接受的 PCM，再允许 ASR 收尾；失败或预算耗尽均明确结束。"""
        joined = asyncio.gather(*(queue.join() for queue in self._queues.values()))
        failed = asyncio.create_task(self.wait_failed())
        try:
            done, _ = await asyncio.wait((joined, failed), timeout=self._send_timeout * 2,
                                        return_when=asyncio.FIRST_COMPLETED)
            if failed in done:
                failed.result()
            if joined not in done:
                raise AudioRelayError("asr_drain_timeout", "等待剩余音频发送超时。")
            await joined
        finally:
            joined.cancel()
            failed.cancel()
            await asyncio.gather(joined, failed, return_exceptions=True)

    async def wait_failed(self) -> None:
        await self._failed.wait()
        assert self._failure is not None
        raise self._failure

    async def close(self) -> None:
        self._closed = True
        tasks = list(self._writers.values())
        if self._optional_notice is not None:
            tasks.append(self._optional_notice)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for channel in self._queues:
            self._discard_queued(channel)
