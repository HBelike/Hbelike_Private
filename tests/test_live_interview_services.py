from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4

from src.career_assistant.live_interview.answer_service import (
    LiveAnswerContext,
    LiveAnswerService,
    build_answer_prompt,
)
from src.career_assistant.live_interview.contracts import (
    AnswerRequestEvent,
    AudioAppendEvent,
    AudioChannel,
    AudioCommitEvent,
    QuestionIntent,
    TranscriptEvent,
)
from src.career_assistant.live_interview.session_manager import LiveSessionManager
from src.career_assistant.live_interview.persistence import LiveInterviewRepository


def test_prompt_requires_chinese_and_forbids_personal_fabrication() -> None:
    prompt = build_answer_prompt(
        "你的业绩是多少？",
        QuestionIntent.BEHAVIORAL,
        LiveAnswerContext(candidate_facts="", target_role="医疗器械产品经理"),
    )

    assert "统一使用中文" in prompt
    assert "不得编造" in prompt
    assert "可替换占位提示" in prompt
    assert "专有名词保留原文" in prompt


def test_prompt_only_uses_transcribed_interviewer_question() -> None:
    prompt = build_answer_prompt(
        "请解释 Kafka consumer group？",
        QuestionIntent.KNOWLEDGE,
        LiveAnswerContext(
            candidate_facts="不应进入提示词的简历内容",
            target_role="不应进入提示词的岗位内容",
            recent_conversation=("不应进入提示词的历史对话",),
            interview_evidence=("不应进入提示词的面经",),
        ),
    )

    assert "请解释 Kafka consumer group？" in prompt
    assert "不应进入提示词" not in prompt
    assert "最近对话：" not in prompt
    assert "已确认个人材料：" not in prompt
    assert "面经检索证据" not in prompt


def test_repository_rejects_partial_before_opening_database_transaction() -> None:
    class DatabaseThatMustNotBeUsed:
        def transaction(self):
            raise AssertionError("partial 不应接触数据库")

    repository = LiveInterviewRepository(DatabaseThatMustNotBeUsed())
    event = TranscriptEvent(AudioChannel.INTERVIEWER, 1, "尚未结束", False)

    try:
        repository.append_final_utterance(uuid4(), uuid4(), uuid4(), event)
    except ValueError as exc:
        assert "final" in str(exc)
    else:
        raise AssertionError("partial 转写被错误持久化")


def test_answer_service_streams_without_buffering_entire_answer() -> None:
    asyncio.run(_assert_answer_service_streams_without_buffering_entire_answer())


async def _assert_answer_service_streams_without_buffering_entire_answer() -> None:
    async def generator(prompt: str) -> AsyncIterator[str]:
        assert "CAP theorem" in prompt
        yield "直接结论："
        yield "CAP 需要权衡。"

    chunks = [
        chunk
        async for chunk in LiveAnswerService(generator).stream(
            "Explain CAP theorem",
            QuestionIntent.KNOWLEDGE,
            LiveAnswerContext(),
        )
    ]

    assert chunks == ["直接结论：", "CAP 需要权衡。"]


class ScriptedAsrSession:
    def __init__(self, transcript: TranscriptEvent) -> None:
        self.transcript = transcript
        self.queue: asyncio.Queue[TranscriptEvent | None] = asyncio.Queue()
        self.closed = False

    async def append_audio(self, pcm: bytes, sequence: int) -> None:
        assert pcm and sequence >= 0

    async def commit(self) -> None:
        await self.queue.put(self.transcript)

    async def events(self) -> AsyncIterator[TranscriptEvent]:
        while True:
            item = await self.queue.get()
            if item is None:
                return
            yield item

    async def close(self) -> None:
        self.closed = True
        await self.queue.put(None)


def test_session_manager_detects_question_and_streams_answer() -> None:
    asyncio.run(_assert_session_manager_detects_question_and_streams_answer())


async def _assert_session_manager_detects_question_and_streams_answer() -> None:
    interviewer = ScriptedAsrSession(
        TranscriptEvent(
            channel=AudioChannel.INTERVIEWER,
            sequence=1,
            text="请解释 CAP theorem？",
            is_final=True,
        )
    )
    candidate = ScriptedAsrSession(
        TranscriptEvent(
            channel=AudioChannel.CANDIDATE,
            sequence=1,
            text="我先思考一下",
            is_final=True,
        )
    )

    async def answer(prompt: str):
        assert "CAP theorem" in prompt
        yield "CAP 的核心是分布式权衡。"

    manager = LiveSessionManager(
        asr_sessions={
            AudioChannel.INTERVIEWER: interviewer,
            AudioChannel.CANDIDATE: candidate,
        },
        answer_service=LiveAnswerService(answer),
    )
    await manager.start()
    await manager.handle(AudioCommitEvent(AudioChannel.INTERVIEWER))

    event_types: list[str] = []
    for _ in range(6):
        event = await asyncio.wait_for(manager.next_event(), timeout=1)
        event_types.append(event.type)
        if event.type == "answer.completed":
            break

    assert event_types[:2] == ["session.ready", "transcript.final"]
    assert "question.detected" in event_types
    assert "answer.delta" in event_types
    assert event_types[-1] == "answer.completed"
    await manager.close("test")
    assert interviewer.closed and candidate.closed


def test_manual_regenerate_increments_attempt_and_close_is_idempotent() -> None:
    asyncio.run(_assert_manual_regenerate_increments_attempt_and_close_is_idempotent())


async def _assert_manual_regenerate_increments_attempt_and_close_is_idempotent() -> None:
    interviewer = ScriptedAsrSession(
        TranscriptEvent(AudioChannel.INTERVIEWER, 1, "背景说明。", True)
    )
    candidate = ScriptedAsrSession(
        TranscriptEvent(AudioChannel.CANDIDATE, 1, "收到。", True)
    )

    async def answer(prompt: str):
        yield "回答"

    manager = LiveSessionManager(
        asr_sessions={
            AudioChannel.INTERVIEWER: interviewer,
            AudioChannel.CANDIDATE: candidate,
        },
        answer_service=LiveAnswerService(answer),
    )
    await manager.start()
    await manager.handle(AnswerRequestEvent(mode="manual", question="请介绍你自己"))
    first_started = await _wait_for_type(manager, "answer.started")
    await _wait_for_type(manager, "answer.completed")
    await manager.handle(AnswerRequestEvent(mode="regenerate"))
    second_started = await _wait_for_type(manager, "answer.started")

    assert first_started.payload["question_version"] == second_started.payload["question_version"]
    assert first_started.payload["attempt"] == 1
    assert second_started.payload["attempt"] == 2
    await manager.close("first")
    await manager.close("second")


async def _wait_for_type(manager: LiveSessionManager, expected: str):
    while True:
        event = await asyncio.wait_for(manager.next_event(), timeout=1)
        if event.type == expected:
            return event
