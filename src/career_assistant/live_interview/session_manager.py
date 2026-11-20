"""单个实时面试 WebSocket 的内存状态与问题版本状态机。"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from contextlib import aclosing

from src.career_assistant.live_interview.answer_service import LiveAnswerContext, LiveAnswerService
from src.career_assistant.live_interview.asr.base import AsrSession
from src.career_assistant.live_interview.contracts import (
    AnswerCancelEvent,
    AnswerRequestEvent,
    AudioAppendEvent,
    AudioChannel,
    AudioCommitEvent,
    ClientEvent,
    DetectedQuestion,
    PingEvent,
    QuestionIntent,
    ServerEvent,
    SessionEndEvent,
    SessionPauseEvent,
    SessionStartEvent,
    TranscriptEvent,
)
from src.career_assistant.live_interview.question_detector import RuleBasedQuestionDetector
from src.career_assistant.live_interview.persistence import LiveInterviewQueuedAnswerState, LiveInterviewResumeState
from src.career_assistant.live_interview.transcript_assembler import TranscriptAssembler


TranscriptHook = Callable[[TranscriptEvent], Awaitable[None]]
AnswerHook = Callable[[int, int, str, QuestionIntent, str, str], Awaitable[None]]
QueueHook = Callable[[LiveInterviewQueuedAnswerState, str], Awaitable[None]]
MAX_PENDING_ANSWERS = 8
ASR_FINISH_TIMEOUT_SECONDS = 5


async def _noop_queue(queued: LiveInterviewQueuedAnswerState, phase: str) -> None:
    return None


async def _noop_transcript(event: TranscriptEvent) -> None:
    return None


async def _noop_answer(
    version: int,
    attempt: int,
    question: str,
    intent: QuestionIntent,
    status: str,
    text: str,
) -> None:
    return None


@dataclass(frozen=True)
class _QueuedAnswer:
    detected: DetectedQuestion
    recent_conversation: tuple[str, ...]
    queued_at: float
    manual: bool = False
    version: int | None = None
    attempt: int = 1
    emit_detected: bool = True
    source_sequence: int | None = None
    record: LiveInterviewQueuedAnswerState | None = None
    previous_question: str | None = None


class LiveSessionManager:
    """每个连接一个实例；负责释放 ASR 与取消迟到回答。"""

    def __init__(
        self,
        *,
        asr_sessions: dict[AudioChannel, AsrSession],
        answer_service: LiveAnswerService,
        answer_context: LiveAnswerContext | None = None,
        transcript_hook: TranscriptHook = _noop_transcript,
        answer_hook: AnswerHook = _noop_answer,
        clock: Callable[[], float] = time.monotonic,
        resume_state: LiveInterviewResumeState | None = None,
        queue_hook: QueueHook = _noop_queue,
    ) -> None:
        self._asr_sessions = asr_sessions
        self._answer_service = answer_service
        self._answer_context = answer_context or LiveAnswerContext()
        self._transcript_hook = transcript_hook
        self._answer_hook = answer_hook
        self._queue_hook = queue_hook
        self._clock = clock
        self._assembler = TranscriptAssembler()
        self._detector = RuleBasedQuestionDetector()
        self._outgoing: asyncio.Queue[ServerEvent] = asyncio.Queue(maxsize=1_000)
        self._reader_tasks: dict[AudioChannel, asyncio.Task[None]] = {}
        self._disabled_channels: set[AudioChannel] = set()
        self._finishing_channels: set[AudioChannel] = set()
        self._close_lock = asyncio.Lock()
        self._answer_task: asyncio.Task[None] | None = None
        self._answer_queue: deque[_QueuedAnswer] = deque()
        self._answer_running = False
        self._interrupted_answer: LiveInterviewQueuedAnswerState | None = None
        self._answer_control_lock = asyncio.Lock()
        self._closed = False
        self._closing_reason = ""
        self._started = False
        resume = resume_state or LiveInterviewResumeState()
        self._next_question_version = resume.max_question_version
        self._question_version = (
            resume.last_question_version if resume.last_question_version is not None else resume.max_question_version
        )
        self._attempt = resume.last_answer_attempt
        self._active_question = resume.last_question
        self._active_intent = resume.last_question_intent
        self._active_recent_conversation = tuple(
            item for item in resume.recent_conversation[-4:]
            if item != f"interviewer: {resume.last_question}"
        )
        latest_pending = max(resume.pending_answers, key=lambda job: job.question_version, default=None)
        self._last_detected_question = (
            latest_pending.question
            if latest_pending and latest_pending.question_version > self._question_version else resume.last_question
        )
        self._completed_suggestions: deque[tuple[str, str]] = deque(maxlen=2)
        if resume.previous_answer:
            self._completed_suggestions.append(resume.previous_answer)
        self._active_previous_question = next((
            item.removeprefix("interviewer: ") for item in reversed(self._active_recent_conversation)
            if item.startswith("interviewer: ")
        ), None)
        self._last_interviewer_final = resume.last_interviewer_final
        self._recent_conversation = list(resume.recent_conversation[-4:])
        self._detector.restore_background(resume.recent_conversation[-4:], self._last_detected_question)
        self._sequence_offsets = {
            channel: resume.max_sequence.get(channel, -1) + 1 for channel in AudioChannel
        }
        self._last_final_sequence = {
            channel: resume.max_sequence.get(channel, -1) for channel in AudioChannel
        }
        self._resumed = bool(resume.max_question_version or resume.recent_conversation)
        self._queue_front_order = min((item.queue_order for item in resume.pending_answers), default=0)
        self._queue_back_order = max((item.queue_order for item in resume.pending_answers), default=0)
        for item in resume.pending_answers:
            age = max(0.0, (datetime.now(UTC) - item.queued_at).total_seconds())
            self._answer_queue.append(_QueuedAnswer(
                detected=DetectedQuestion(item.question, item.intent, 1.0, item.intent is QuestionIntent.FOLLOW_UP),
                recent_conversation=item.recent_conversation[-4:],
                queued_at=self._clock() - age,
                version=item.question_version,
                attempt=item.attempt,
                manual=item.manual,
                emit_detected=item.emit_detected,
                source_sequence=item.source_sequence,
                record=item,
                previous_question=item.previous_question or next((
                    line.removeprefix("interviewer: ") for line in reversed(item.recent_conversation)
                    if line.startswith("interviewer: ")
                ), resume.last_question),
            ))

    @property
    def closed(self) -> bool:
        return self._closed

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        for channel, session in self._asr_sessions.items():
            self._reader_tasks[channel] = asyncio.create_task(
                self._read_asr(channel, session), name=f"live-asr-{channel.value}"
            )
        await self._emit(
            "session.ready",
            sample_rate=24_000,
            active_channels=[channel.value for channel in self._asr_sessions],
            resumed=self._resumed,
            question_version=self._question_version,
            answer_attempt=self._attempt,
            current_question=self._active_question or "",
            question_intent=self._active_intent.value,
        )
        await self._emit_queue()
        self._ensure_answer_worker()

    async def next_event(self) -> ServerEvent:
        return await self._outgoing.get()

    async def handle(self, event: ClientEvent) -> None:
        if self._closed or self._closing_reason:
            return
        if isinstance(event, SessionStartEvent):
            await self.start()
        elif isinstance(event, (AudioAppendEvent, AudioCommitEvent)):
            if event.channel in self._disabled_channels:
                return
            if event.channel not in self._asr_sessions:
                raise ValueError(f"当前会话未启用 {event.channel.value} 音频轨道")
            session = self._asr_sessions[event.channel]
            try:
                if isinstance(event, AudioAppendEvent):
                    await session.append_audio(event.pcm, event.sequence)
                else:
                    await session.commit()
            except Exception:
                if event.channel is not AudioChannel.CANDIDATE:
                    raise
                await self.disable_channel(event.channel, "候选人语音识别已结束，继续采集面试官声音。")
        elif isinstance(event, AnswerRequestEvent):
            await self._handle_answer_request(event)
        elif isinstance(event, AnswerCancelEvent):
            async with self._answer_control_lock:
                await self._cancel_answer("user")
                await self._emit_queue()
                self._ensure_answer_worker()
        elif isinstance(event, PingEvent):
            await self._emit("pong")
        elif isinstance(event, (SessionPauseEvent, SessionEndEvent)):
            await self.close("pause" if isinstance(event, SessionPauseEvent) else "user")
            await self._outgoing.put(ServerEvent("session.ended", {"reason": self._closing_reason}))

    async def disable_channel(self, channel: AudioChannel, message: str) -> None:
        """释放失效的可选音轨，主音轨继续接收与作答。"""
        if channel is not AudioChannel.CANDIDATE:
            raise ValueError("面试官音轨不能单独关闭")
        if channel in self._disabled_channels or channel not in self._asr_sessions:
            return
        self._disabled_channels.add(channel)
        await self._emit("channel.ended", channel=channel.value, message=message)
        reader = self._reader_tasks.get(channel)
        if reader is not None and reader is not asyncio.current_task():
            reader.cancel()
            await asyncio.gather(reader, return_exceptions=True)
        await asyncio.gather(self._asr_sessions[channel].close(), return_exceptions=True)

    async def finish_channel(self, channel: AudioChannel) -> None:
        if channel is not AudioChannel.CANDIDATE:
            raise ValueError("仅候选人音轨可以单独停止")
        if channel not in self._asr_sessions or channel in self._disabled_channels:
            return
        await self._finish_input(channel)
        await self.disable_channel(channel, "候选人麦克风已停止，继续采集面试官声音。")

    async def _read_asr(self, channel: AudioChannel, session: AsrSession) -> None:
        try:
            async for event in session.events():
                if self._closed:
                    return
                accepted = self._assembler.accept(event)
                if accepted is not None:
                    preserve_partial = self._assembler.has_newer_partial(accepted)
                    sequence = accepted.sequence + self._sequence_offsets[channel]
                    if accepted.is_final:
                        # 顺序校验使用上游原序号；通过后才分配唯一持久化游标。
                        sequence = max(sequence, self._last_final_sequence[channel] + 1)
                        self._last_final_sequence[channel] = sequence
                    await self._handle_transcript(replace(accepted, sequence=sequence), preserve_partial)
            code = "asr_stream_ended"
        except asyncio.CancelledError:
            raise
        except Exception:
            code = "asr_stream_failed"
            if channel in self._finishing_channels:
                await self._emit("error", code="asr_finish_incomplete", fatal=False, channel=channel.value,
                                 message="语音服务未确认收尾，最后一句可能不完整。")
        if self._closing_reason or channel in self._finishing_channels:
            return
        if channel is AudioChannel.CANDIDATE:
            await self.disable_channel(channel, "候选人语音识别已中断，继续采集面试官声音。")
        else:
            await self._emit("error", code=code, fatal=True, message="面试官语音识别已中断，正在重新连接。")

    async def _handle_transcript(self, event: TranscriptEvent, preserve_partial: bool) -> None:
        await self._emit(
            "transcript.final" if event.is_final else "transcript.partial",
            channel=event.channel.value,
            role=event.role.value,
            sequence=event.sequence,
            text=event.text,
            **({"preserve_partial": True} if preserve_partial else {}),
        )
        if not event.is_final:
            return
        # final 已展示后，必须把转写落库和 queued 标记一起完成；断线不截断半条链路。
        task = asyncio.create_task(self._accept_final_transcript(event))
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await task
            raise

    async def _accept_final_transcript(self, event: TranscriptEvent) -> None:
        prior_conversation = tuple(self._recent_conversation[-4:])
        self._recent_conversation.append(f"{event.role.value}: {event.text}")
        self._recent_conversation = self._recent_conversation[-12:]
        try:
            await self._transcript_hook(event)
        except Exception:
            await self._emit("error", code="transcript_save_failed", fatal=False, sequence=event.sequence,
                             message="本句转写未能保存到历史记录，实时采集继续。")
        if event.channel is AudioChannel.INTERVIEWER:
            self._last_interviewer_final = event.text
        detected = self._detector.detect(event, self._last_detected_question)
        if detected is not None:
            previous = self._last_detected_question
            self._last_detected_question = detected.normalized_question
            async with self._answer_control_lock:
                await self._enqueue_answer(_QueuedAnswer(
                    detected=detected,
                    recent_conversation=prior_conversation,
                    queued_at=self._clock(),
                    source_sequence=event.sequence,
                    previous_question=previous,
                ))
            await self._emit_queue()
            self._ensure_answer_worker()

    async def _activate_job(self, job: _QueuedAnswer) -> None:
        assert job.version is not None
        if job.record is not None:
            await self._persist_queue_phase(job.record, "started")
        self._question_version = job.version
        self._attempt = job.attempt
        self._active_question = job.detected.normalized_question
        self._active_intent = job.detected.intent
        self._active_recent_conversation = job.recent_conversation
        self._active_previous_question = job.previous_question

    async def _publish_active_job(self, job: _QueuedAnswer) -> None:
        if job.emit_detected:
            await self._emit(
                "question.detected",
                question_version=self._question_version,
                question=self._active_question,
                intent=job.detected.intent.value,
                confidence=job.detected.confidence,
                is_follow_up=job.detected.is_follow_up,
                manual=job.manual,
                source_sequence=job.source_sequence,
            )

    async def _handle_answer_request(self, event: AnswerRequestEvent) -> None:
        async with self._answer_control_lock:
            await self._apply_answer_request(event)

    async def _apply_answer_request(self, event: AnswerRequestEvent) -> None:
        if event.mode == "regenerate":
            if self._active_question is None:
                raise ValueError("当前没有可重新生成的问题")
            question = self._active_question
            intent = self._active_intent
            recent_conversation = self._active_recent_conversation
            version = self._question_version
            superseded = tuple(job for job in self._answer_queue if job.version == version and not job.emit_detected)
            attempt = max((self._attempt, *(job.attempt for job in superseded))) + 1
            await self._cancel_answer("regenerate")
            for job in superseded:
                if job.record is not None:
                    await self._persist_queue_phase(job.record, "cancelled")
            superseded_ids = {id(job) for job in superseded}
            self._answer_queue = deque(job for job in self._answer_queue if id(job) not in superseded_ids)
            await self._enqueue_answer(
                _QueuedAnswer(
                    detected=DetectedQuestion(
                        normalized_question=question,
                        intent=intent,
                        confidence=1.0,
                        is_follow_up=intent is QuestionIntent.FOLLOW_UP,
                    ),
                    recent_conversation=recent_conversation,
                    queued_at=self._clock(),
                    version=version,
                    attempt=attempt,
                    emit_detected=False,
                    previous_question=self._active_previous_question,
                ), front=True,
            )
        else:
            question = event.question or self._last_interviewer_final
            if not question:
                raise ValueError("尚未收到可用于生成的面试官话语")
            previous = self._last_detected_question
            background = self._detector.take_background()
            if question == self._last_interviewer_final:
                if background:
                    question = background
                elif previous and previous.endswith(question):
                    question = previous
            await self._cancel_answer("manual_replaced")
            superseded = tuple(job for job in self._answer_queue if job.detected.normalized_question == question)
            for job in superseded:
                if job.record is not None:
                    await self._persist_queue_phase(job.record, "cancelled")
            superseded_ids = {id(job) for job in superseded}
            self._answer_queue = deque(job for job in self._answer_queue if id(job) not in superseded_ids)
            recent_conversation = tuple(self._recent_conversation[-4:])
            if superseded:
                original = superseded[-1]
                previous = original.previous_question
                recent_conversation = original.recent_conversation
                intent = original.detected.intent
            elif question == self._active_question:
                previous = self._active_previous_question
                recent_conversation = self._active_recent_conversation
                intent = self._active_intent
            else:
                if question == self._last_interviewer_final and recent_conversation:
                    if recent_conversation[-1] == f"interviewer: {question}":
                        recent_conversation = tuple(self._recent_conversation[-5:-1])
                intent = self._detector.classify(question, previous)
            detected = DetectedQuestion(
                normalized_question=question,
                intent=intent,
                confidence=1.0,
                is_follow_up=intent is QuestionIntent.FOLLOW_UP,
            )
            self._last_detected_question = question
            await self._enqueue_answer(
                _QueuedAnswer(
                    detected=detected,
                    recent_conversation=recent_conversation,
                    queued_at=self._clock(),
                    manual=True,
                    previous_question=previous,
                ), front=True,
            )
        await self._emit_queue()
        self._ensure_answer_worker()

    async def _enqueue_answer(self, job: _QueuedAnswer, *, front: bool = False) -> None:
        if job.version is None:
            self._next_question_version += 1
            job = replace(job, version=self._next_question_version)
        assert job.version is not None
        if front:
            self._queue_front_order -= 1
            queue_order = self._queue_front_order
        else:
            self._queue_back_order += 1
            queue_order = self._queue_back_order
        record = LiveInterviewQueuedAnswerState(
            question_version=job.version,
            attempt=job.attempt,
            question=job.detected.normalized_question,
            intent=job.detected.intent,
            recent_conversation=job.recent_conversation[-4:],
            queued_at=datetime.now(UTC),
            source_sequence=job.source_sequence,
            queue_order=queue_order,
            manual=job.manual,
            emit_detected=job.emit_detected,
            previous_question=job.previous_question,
        )
        if len(self._answer_queue) >= MAX_PENDING_ANSWERS:
            # 保留用户优先请求及当前作答，让最新自动问题能够跟上现场。
            skipped = next((pending for pending in self._answer_queue if not pending.manual), None)
            if skipped is None:
                await self._persist_queue_phase(record, "cancelled")
            else:
                self._answer_queue.remove(skipped)
                if skipped.record:
                    await self._persist_queue_phase(skipped.record, "cancelled")
            await self._emit("error", code="answer_queue_full", fatal=False,
                             question_version=skipped.version if skipped else job.version,
                             message="待答题已满，已跳过一道待答题；原题可在本场转写和归档中查看。")
            if skipped is None:
                return
        # 入内存 FIFO 前先保存明确 queued 标记，断线恢复不靠历史文本猜测。
        try:
            await self._persist_queue_phase(record, "queued")
        except asyncio.CancelledError:
            # 已提交但尚未入内存的 queued：主动结束需撤销，断线则保留供恢复。
            if self._closed and self._closing_reason == "user":
                await self._persist_queue_phase(record, "cancelled")
            raise
        job = replace(job, record=record)
        if front:
            self._answer_queue.appendleft(job)
        else:
            self._answer_queue.append(job)

    async def _persist_queue_phase(self, record: LiveInterviewQueuedAnswerState, phase: str) -> None:
        await self._write_answer_history(self._queue_hook(record, phase))

    async def _persist_answer_state(self, version, attempt, question, intent, status, text) -> None:
        await self._write_answer_history(self._answer_hook(version, attempt, question, intent, status, text))

    async def _write_answer_history(self, write: Awaitable[None]) -> None:
        # to_thread 被取消后事务可能仍提交；先等写入落定，后续状态才不会被晚提交覆盖。
        task = asyncio.ensure_future(write)
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            await asyncio.gather(task, return_exceptions=True)
            raise
        except Exception:
            await self._emit("error", code="answer_save_failed", fatal=False,
                             message="本题历史保存失败，实时作答继续；重连后可能缺少这道题的记录。")

    def _ensure_answer_worker(self) -> None:
        if self._closed or self._closing_reason or not self._answer_queue:
            return
        if self._answer_task is not None and not self._answer_task.done():
            return
        self._answer_task = asyncio.create_task(
            self._drain_answer_queue(),
            name="live-answer-worker",
        )

    async def _drain_answer_queue(self) -> None:
        worker = asyncio.current_task()
        try:
            while self._answer_queue and not self._closing_reason:
                async with self._answer_control_lock:
                    if self._closing_reason or not self._answer_queue:
                        break
                    job = self._answer_queue.popleft()
                    try:
                        await self._activate_job(job)
                    except asyncio.CancelledError:
                        # 临界段被外部取消时没有调用模型，恢复 queued 供下次继续。
                        if job.record is not None:
                            await self._persist_queue_phase(job.record, "queued")
                        self._answer_queue.appendleft(job)
                        raise
                    self._answer_running = True
                    self._interrupted_answer = job.record
                entered_generation = False
                try:
                    await self._emit_queue()
                    await self._publish_active_job(job)
                    question = self._active_question
                    assert question is not None
                    entered_generation = True
                    await self._run_answer(
                        self._question_version,
                        self._attempt,
                        question,
                        self._active_intent,
                        job.recent_conversation,
                        job.queued_at,
                    )
                except asyncio.CancelledError:
                    if not entered_generation:
                        await self._persist_answer_state(
                            self._question_version, self._attempt, self._active_question or "",
                            self._active_intent, "cancelled", "",
                        )
                    raise
                finally:
                    self._answer_running = False
                    await self._emit_queue()
        finally:
            if self._answer_task is worker:
                self._answer_task = None

    async def _run_answer(
        self,
        version: int,
        attempt: int,
        question: str,
        intent: QuestionIntent,
        recent_conversation: tuple[str, ...],
        queued_at: float,
    ) -> None:
        text = ""
        started_at = self._clock()
        generated = False
        try:
            await self._persist_answer_state(version, attempt, question, intent, "generating", text)
            await self._emit(
                "answer.started",
                question_version=version,
                attempt=attempt,
                queue_wait_ms=max(0, round((started_at - queued_at) * 1_000)),
            )
            context = LiveAnswerContext(
                candidate_facts=self._answer_context.candidate_facts,
                target_role=self._answer_context.target_role,
                recent_conversation=recent_conversation,
                interview_evidence=self._answer_context.interview_evidence,
                terminology=self._answer_context.terminology,
                previous_question=self._active_previous_question or "",
                reference_answer=next((
                    suggestion for source, suggestion in reversed(self._completed_suggestions)
                    if source == self._active_previous_question
                ), ""),
            )
            delta_index = 0
            async with aclosing(self._answer_service.stream(question, intent, context)) as stream:
                async for chunk in stream:
                    if version != self._question_version or attempt != self._attempt:
                        return
                    text += chunk
                    delta_index += 1
                    payload: dict[str, object] = {
                        "question_version": version,
                        "attempt": attempt,
                        "delta_index": delta_index,
                        "delta": chunk,
                    }
                    if delta_index == 1:
                        # 已开始输出的答案保留 partial，由用户决定是否重新调用模型。
                        self._interrupted_answer = None
                        payload["first_token_ms"] = max(
                            0,
                            round((self._clock() - started_at) * 1_000),
                        )
                    await self._emit("answer.delta", **payload)
            generated = True
            self._interrupted_answer = None
            await self._persist_answer_state(version, attempt, question, intent, "completed", text)
            if self._completed_suggestions and self._completed_suggestions[-1][0] == question:
                self._completed_suggestions.pop()
            self._completed_suggestions.append((question, text[:800]))
            await self._emit(
                "answer.completed",
                question_version=version,
                attempt=attempt,
                answer_text=text,
                generation_ms=max(0, round((self._clock() - started_at) * 1_000)),
            )
        except asyncio.CancelledError:
            if not generated:
                await self._persist_answer_state(version, attempt, question, intent, "cancelled", text)
            raise
        except Exception:
            self._interrupted_answer = None
            await self._persist_answer_state(version, attempt, question, intent, "failed", text)
            await self._emit(
                "error",
                code="answer_failed",
                message="回答生成失败，可点击重新生成",
                question_version=version,
            )

    async def _cancel_answer(self, reason: str) -> None:
        task = self._answer_task
        if task is None or task.done():
            return
        version, attempt = self._question_version, self._attempt
        had_active_answer = self._answer_running and version > 0 and attempt > 0
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        if reason in {"disconnect", "pause"} and self._interrupted_answer and self._interrupted_answer.attempt == 1:
            # 仅在首个 attempt 尚未输出回答时自动恢复一次。
            record = self._interrupted_answer
            self._queue_front_order -= 1
            retry = replace(record, attempt=record.attempt + 1, queue_order=self._queue_front_order,
                            emit_detected=True)
            await self._persist_queue_phase(retry, "queued")
        self._interrupted_answer = None
        if had_active_answer and not self._outgoing.full():
            await self._emit(
                "answer.cancelled",
                question_version=version,
                attempt=attempt,
                reason=reason,
            )
        if self._answer_task is task:
            self._answer_task = None

    async def _emit(self, event_type: str, **payload: object) -> None:
        if self._closed:
            return
        if event_type == "error" and not payload.get("fatal") and self._outgoing.full():
            # 发送端已阻塞时，历史保存告警不能阻塞 final 的收尾事务。
            return
        await self._outgoing.put(ServerEvent(event_type, dict(payload)))

    async def _emit_queue(self) -> None:
        # 满队列时不再追加可重建的队列状态，避免取消路径等待已阻塞的消费者。
        if self._outgoing.full():
            return
        oldest = min((job.queued_at for job in self._answer_queue), default=self._clock())
        await self._emit(
            "answer.queue",
            pending_count=len(self._answer_queue),
            oldest_wait_ms=max(0, round((self._clock() - oldest) * 1_000)),
        )

    async def _finish_input(self, channel: AudioChannel) -> None:
        session = self._asr_sessions[channel]
        reader = self._reader_tasks.get(channel)
        finish = getattr(session, "finish", None)
        if reader is None or reader.done() or channel in self._disabled_channels or finish is None:
            return
        self._finishing_channels.add(channel)
        try:
            async with asyncio.timeout(ASR_FINISH_TIMEOUT_SECONDS):
                await finish()
                await asyncio.shield(reader)
        except Exception:
            await self._emit("error", code="asr_finish_incomplete", fatal=False, channel=channel.value,
                             message="语音服务未确认收尾，最后一句可能不完整。")

    async def close(self, reason: str = "closed") -> None:
        async with self._close_lock:
            if self._closed:
                return
            self._closing_reason = reason
            try:
                if reason in {"user", "pause"}:
                    await asyncio.gather(*(self._finish_input(ch) for ch in self._asr_sessions))
            finally:
                self._closed = True
                # 等展示过的 final 入库；断线后不再向已停止的发送器投递事件。
                for task in self._reader_tasks.values():
                    task.cancel()
                await asyncio.gather(*self._reader_tasks.values(), return_exceptions=True)
                async with self._answer_control_lock:
                    if reason == "user":
                        for job in self._answer_queue:
                            if job.record is not None:
                                await self._persist_queue_phase(job.record, "cancelled")
                    self._answer_queue.clear()
                    await self._cancel_answer(reason)
                await asyncio.gather(
                    *(session.close() for session in self._asr_sessions.values()),
                    return_exceptions=True,
                )
