from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.career_assistant.live_interview.answer_service import LiveAnswerService
from src.career_assistant.live_interview.contracts import (
    AnswerRequestEvent, AnswerStatus, AudioChannel, QuestionIntent, TranscriptEvent,
    ServerEvent,
)
from src.career_assistant.live_interview.persistence import (
    LiveInterviewQueuedAnswerState, LiveInterviewRepository, LiveInterviewResumeState,
)
from src.career_assistant.live_interview.session_manager import LiveSessionManager
from src.career_assistant.persistence.database import CareerDatabase


def local_test_database():
    database_url = os.getenv("EXTERNAL_CHAT_TEST_DATABASE_URL", "")
    target = urlsplit(database_url)
    isolated = target.path == "/interview_verify" or target.path.startswith("/skill_release_")
    if target.hostname not in {"127.0.0.1", "localhost", "::1"} or not isolated:
        pytest.skip("需要本地隔离 PostgreSQL 测试库")
    return CareerDatabase(database_url)


class ControlledAsr:
    def __init__(self):
        self.queue = asyncio.Queue()
        self.closed = False

    async def events(self):
        while True:
            value = await self.queue.get()
            if value is None:
                return
            if isinstance(value, Exception):
                raise value
            yield value

    async def append_audio(self, pcm, sequence):
        return None

    async def close(self):
        self.closed = True


async def event_matching(manager, event_type, predicate=lambda event: True):
    for _ in range(100):
        event = await asyncio.wait_for(manager.next_event(), 1)
        if event.type == event_type and predicate(event):
            return event
    raise AssertionError(f"未收到 {event_type}")


@pytest.mark.parametrize("ending,code", [(None, "asr_stream_ended"), (RuntimeError("stopped"), "asr_stream_failed")])
def test_unexpected_asr_exit_is_fatal_but_user_close_is_not(ending, code):
    async def scenario():
        async def answer(prompt):
            yield "未调用"

        asr = ControlledAsr()
        manager = LiveSessionManager(
            asr_sessions={AudioChannel.INTERVIEWER: asr}, answer_service=LiveAnswerService(answer),
        )
        await manager.start()
        await asr.queue.put(ending)
        error = await event_matching(manager, "error")
        assert error.payload["code"] == code
        assert error.payload["fatal"] is True
        await manager.close("user")
        while not manager._outgoing.empty():
            assert (await manager.next_event()).type != "error"
        assert asr.closed

    asyncio.run(scenario())


def test_resume_preserves_four_turns_versions_and_unique_final_sequences_without_reanswer():
    async def scenario():
        history = tuple(f"interviewer: 旧问题{index}" for index in range(4))
        resume = LiveInterviewResumeState(
            recent_conversation=history,
            last_interviewer_final="旧问题3",
            last_question="旧问题3",
            last_question_intent=QuestionIntent.FOLLOW_UP,
            max_question_version=5,
            last_answer_attempt=2,
            max_sequence={AudioChannel.INTERVIEWER: 50},
        )
        prompts, saved = [], []

        async def answer(prompt):
            prompts.append(prompt)
            yield "新回答"

        async def save(event):
            saved.append(event)

        asr = ControlledAsr()
        manager = LiveSessionManager(
            asr_sessions={AudioChannel.INTERVIEWER: asr}, answer_service=LiveAnswerService(answer),
            resume_state=resume, transcript_hook=save,
        )
        await manager.start()
        ready = await event_matching(manager, "session.ready")
        assert ready.payload["resumed"] is True
        assert ready.payload["question_version"] == 5
        assert ready.payload["answer_attempt"] == 2
        await asyncio.sleep(0)
        assert not prompts
        await asr.queue.put(TranscriptEvent(AudioChannel.INTERVIEWER, 0, "继续讲这个问题。", True))
        completed = await event_matching(manager, "answer.completed")
        assert completed.payload["question_version"] == 6
        assert all(item in prompts[0] for item in history)
        assert saved[0].sequence == 51
        # 上游补发多句、复用音频序号时，最终持久化游标仍保持唯一。
        await asr.queue.put(TranscriptEvent(AudioChannel.INTERVIEWER, 0, "进一步解释另一种方案。", True))
        completed = await event_matching(manager, "answer.completed")
        assert completed.payload["question_version"] == 7
        assert saved[1].sequence == 52
        await asr.queue.put(TranscriptEvent(AudioChannel.INTERVIEWER, 2, "怎么避免缓存穿透？", True))
        await event_matching(manager, "answer.completed")
        # 旧 final 不能因持久化序号递增而绕过原顺序门禁。
        await asr.queue.put(TranscriptEvent(AudioChannel.INTERVIEWER, 1, "乱序旧问题不可回答。", True))
        await asr.queue.put(TranscriptEvent(AudioChannel.INTERVIEWER, 2, "如何处理缓存雪崩？", True))
        completed = await event_matching(manager, "answer.completed")
        assert completed.payload["question_version"] == 9
        assert [event.sequence for event in saved] == [51, 52, 53, 54]
        assert all("乱序旧问题" not in prompt for prompt in prompts)
        await manager.close()

    asyncio.run(scenario())


def test_resume_regenerate_increments_old_attempt():
    async def scenario():
        async def answer(prompt):
            yield "重新生成"

        manager = LiveSessionManager(
            asr_sessions={}, answer_service=LiveAnswerService(answer),
            resume_state=LiveInterviewResumeState(last_question="旧问题", max_question_version=7, last_answer_attempt=2),
        )
        await manager.start()
        await manager.handle(AnswerRequestEvent("regenerate"))
        completed = await event_matching(manager, "answer.completed")
        assert completed.payload["question_version"] == 7
        assert completed.payload["attempt"] == 3
        await manager.close()

    asyncio.run(scenario())


def test_queue_reports_waiting_jobs_excluding_active_answer():
    async def scenario():
        first_release = asyncio.Event()
        now = [100.0]

        async def answer(prompt):
            if "问题1" in prompt:
                await first_release.wait()
            yield "回答"

        asr = ControlledAsr()
        manager = LiveSessionManager(
            asr_sessions={AudioChannel.INTERVIEWER: asr}, answer_service=LiveAnswerService(answer),
            clock=lambda: now[0],
        )
        await manager.start()
        await asr.queue.put(TranscriptEvent(AudioChannel.INTERVIEWER, 0, "问题1怎么解决？", True))
        await event_matching(manager, "answer.started")
        await asr.queue.put(TranscriptEvent(AudioChannel.INTERVIEWER, 1, "问题2怎么解决？", True))
        await event_matching(manager, "answer.queue", lambda e: e.payload["pending_count"] == 1)
        now[0] += 0.5
        await asr.queue.put(TranscriptEvent(AudioChannel.INTERVIEWER, 2, "问题3怎么解决？", True))
        queued = await event_matching(manager, "answer.queue", lambda e: e.payload["pending_count"] == 2)
        assert queued.payload["oldest_wait_ms"] == 500
        first_release.set()
        for _ in range(3):
            await event_matching(manager, "answer.completed")
        empty = await event_matching(manager, "answer.queue", lambda e: e.payload["pending_count"] == 0)
        assert empty.payload["oldest_wait_ms"] == 0
        await manager.close()

    asyncio.run(scenario())


def test_disconnect_releases_worker_when_outgoing_client_queue_is_full():
    async def scenario():
        async def answer(prompt):
            await asyncio.Event().wait()
            yield "未执行"

        manager = LiveSessionManager(asr_sessions={}, answer_service=LiveAnswerService(answer))
        await manager.start()
        await manager.handle(AnswerRequestEvent("manual", "测试问题"))
        await event_matching(manager, "answer.started")
        while not manager._outgoing.full():
            manager._outgoing.put_nowait(ServerEvent("pong"))
        await asyncio.wait_for(manager.close("disconnect"), 1)
        assert manager.closed
        assert manager._answer_task is None

    asyncio.run(scenario())


def test_rapid_regenerate_replaces_reserved_attempt_without_duplicate_invocation():
    async def scenario():
        invoked, phases = [], []

        async def answer(prompt):
            invoked.append(prompt)
            yield "最新重生成"

        async def queue_hook(queued, phase):
            phases.append((queued.question_version, queued.attempt, phase))

        manager = LiveSessionManager(
            asr_sessions={}, answer_service=LiveAnswerService(answer), queue_hook=queue_hook,
            resume_state=LiveInterviewResumeState(last_question="问题", max_question_version=1, last_answer_attempt=1),
        )
        await manager.start()
        await manager.handle(AnswerRequestEvent("regenerate"))
        await manager.handle(AnswerRequestEvent("regenerate"))
        completed = await event_matching(manager, "answer.completed")
        assert completed.payload["question_version"] == 1
        assert completed.payload["attempt"] == 3
        assert len(invoked) == 1
        assert (1, 2, "cancelled") in phases
        assert (1, 3, "started") in phases
        await manager.close()

    asyncio.run(scenario())


def test_repository_resume_uses_only_four_transcripts_and_reopens_same_session():
    database = local_test_database()
    repository = LiveInterviewRepository(database)
    organization_id, actor_id = uuid4(), uuid4()
    session = repository.create_session(
        organization_id, actor_id, candidate_profile_id=None, target_role_profile_id=None,
    )
    try:
        assert repository.activate(organization_id, actor_id, session.id)
        started_at = repository.get_session(organization_id, actor_id, session.id).started_at
        for index in range(6):
            channel = AudioChannel.INTERVIEWER if index % 2 == 0 else AudioChannel.CANDIDATE
            repository.append_final_utterance(
                organization_id, actor_id, session.id, TranscriptEvent(channel, index, f"转写{index}", True),
            )
        repository.upsert_answer(
            organization_id, actor_id, session.id, question_version=9, attempt=3,
            question="历史问题", intent=QuestionIntent.FOLLOW_UP, status=AnswerStatus.COMPLETED,
            answer_text="有界历史建议" * 200,
        )
        repository.end(organization_id, actor_id, session.id, failed=True)
        assert repository.resume_state(organization_id, uuid4(), session.id) is None
        assert repository.activate(organization_id, actor_id, session.id)
        reopened = repository.get_session(organization_id, actor_id, session.id)
        assert reopened.started_at == started_at
        assert reopened.ended_at is None
        resume = repository.resume_state(organization_id, actor_id, session.id)
        assert resume.recent_conversation == (
            "interviewer: 转写2", "candidate: 转写3", "interviewer: 转写4", "candidate: 转写5",
        )
        assert resume.last_interviewer_final == "转写4"
        assert resume.max_question_version == 9
        assert resume.last_answer_attempt == 3
        assert resume.max_sequence == {AudioChannel.INTERVIEWER: 4, AudioChannel.CANDIDATE: 5}
        assert resume.previous_answer == ("历史问题", ("有界历史建议" * 200)[:800])
    finally:
        with database.transaction() as connection:
            connection.execute(
                text("DELETE FROM career_assistant.live_interview_sessions WHERE id = :id"), {"id": session.id},
            )
        database.close()


def test_postgres_claim_is_exclusive_and_completed_session_stays_closed():
    database = local_test_database()
    repository = LiveInterviewRepository(database)
    org, actor = uuid4(), uuid4()
    session = repository.create_session(org, actor, candidate_profile_id=None, target_role_profile_id=None)
    barrier = Barrier(2)

    def claim():
        barrier.wait(timeout=3)
        return repository.activate(org, actor, session.id)

    try:
        with ThreadPoolExecutor(max_workers=2) as threads:
            attempts = [threads.submit(claim) for _ in range(2)]
            assert sorted(attempt.result(timeout=5) for attempt in attempts) == [False, True]
        assert not repository.finish_inactive(org, actor, session.id)
        final = TranscriptEvent(AudioChannel.INTERVIEWER, 1, "如何实现限流？", True)
        first = repository.append_final_utterance(org, actor, session.id, final)
        assert repository.append_final_utterance(org, actor, session.id, final) == first
        assert len(repository.history(org, actor, session.id)["utterances"]) == 1
        assert repository.pause(org, actor, session.id)
        assert repository.activate(org, actor, session.id)
        assert repository.end(org, actor, session.id, failed=True)
        assert not repository.finish_inactive(org, uuid4(), session.id)
        assert repository.finish_inactive(org, actor, session.id)
        assert repository.finish_inactive(org, actor, session.id)
        assert not repository.activate(org, actor, session.id)
    finally:
        with database.transaction() as connection:
            connection.execute(text("DELETE FROM career_assistant.live_interview_sessions WHERE id=:id"), {"id": session.id})
        database.close()


def test_postgres_pending_fifo_restores_snapshots_and_manual_replacement_does_not_reanswer():
    database = local_test_database()
    repository = LiveInterviewRepository(database)
    organization_id, actor_id = uuid4(), uuid4()
    session = repository.create_session(
        organization_id, actor_id, candidate_profile_id=None, target_role_profile_id=None,
    )

    async def scenario():
        repository.upsert_answer(
            organization_id, actor_id, session.id, question_version=1, attempt=1,
            question="旧题不可重答", intent=QuestionIntent.KNOWLEDGE, status=AnswerStatus.COMPLETED,
            answer_text="旧答案不可进入记忆",
        )

        def queue_record(version, history):
            return LiveInterviewQueuedAnswerState(
                question_version=version, attempt=1, question="继续讲一致性", intent=QuestionIntent.FOLLOW_UP,
                recent_conversation=(history,), queued_at=datetime.now(UTC) - timedelta(seconds=20),
                source_sequence=version, queue_order=version,
            )

        async def save_queue(queued, phase):
            await asyncio.to_thread(repository.save_queued_answer, organization_id, actor_id, session.id, queued, phase)

        async def save_answer(version, attempt, question, intent, status, answer_text):
            await asyncio.to_thread(
                repository.upsert_answer, organization_id, actor_id, session.id,
                question_version=version, attempt=attempt, question=question,
                intent=intent, status=AnswerStatus(status), answer_text=answer_text,
            )

        for version, history in ((2, "interviewer: 题2之前"), (3, "interviewer: 题3之前")):
            repository.save_queued_answer(organization_id, actor_id, session.id, queue_record(version, history), "queued")
        restored = repository.resume_state(organization_id, actor_id, session.id)
        assert restored.max_question_version == 3
        assert restored.last_question_version == 1
        assert len(restored.pending_answers) == 2
        prompts = []

        async def answer(prompt):
            prompts.append(prompt)
            yield "完成"

        manager = LiveSessionManager(
            asr_sessions={}, answer_service=LiveAnswerService(answer), resume_state=restored,
            queue_hook=save_queue, answer_hook=save_answer,
        )
        await manager.start()
        completed = [(await event_matching(manager, "answer.completed")).payload["question_version"] for _ in range(2)]
        assert completed == [2, 3]
        assert "题2之前" in prompts[0] and "题3之前" not in prompts[0]
        assert "题3之前" in prompts[1] and "题2之前" not in prompts[1]
        assert all("旧题不可重答" not in prompt and "旧答案不可进入记忆" not in prompt for prompt in prompts)
        await manager.close("disconnect")
        assert not repository.resume_state(organization_id, actor_id, session.id).pending_answers

        for version in (4, 5):
            repository.save_queued_answer(organization_id, actor_id, session.id, queue_record(version, "interviewer: 早先"), "queued")
        calls = []

        async def paused_answer(prompt):
            calls.append(prompt)
            if len(calls) == 1:
                await asyncio.Event().wait()
            yield "手动回答"

        manager = LiveSessionManager(
            asr_sessions={}, answer_service=LiveAnswerService(paused_answer),
            resume_state=repository.resume_state(organization_id, actor_id, session.id),
            queue_hook=save_queue, answer_hook=save_answer,
        )
        await manager.start()
        assert (await event_matching(manager, "answer.started")).payload["question_version"] == 4
        await manager.handle(AnswerRequestEvent("manual", "继续讲一致性"))
        completed = await event_matching(manager, "answer.completed")
        assert completed.payload["question_version"] == 6
        await manager.close("disconnect")
        assert len(calls) == 2
        resume = repository.resume_state(organization_id, actor_id, session.id)
        assert not resume.pending_answers
        assert resume.max_question_version == 6
        history = repository.history(organization_id, actor_id, session.id)
        cancelled = next(row for row in history["answers"] if row["question_version"] == 5)
        assert cancelled["status"] == "cancelled"
        assert cancelled["evidence_json"][0]["phase"] == "cancelled"

        auto = replace(queue_record(7, "interviewer: 自动题之前"), queue_order=1)
        manual = replace(queue_record(8, "interviewer: 手动题之前"), queue_order=-1, manual=True)
        regenerated = replace(queue_record(6, "interviewer: 重生题之前"), attempt=2, queue_order=-2, emit_detected=False)
        for queued in (auto, manual, regenerated):
            repository.save_queued_answer(organization_id, actor_id, session.id, queued, "queued")
        resumed = repository.resume_state(organization_id, actor_id, session.id)
        assert [(row.question_version, row.attempt) for row in resumed.pending_answers] == [(6, 2), (8, 1), (7, 1)]
        assert resumed.max_question_version == 8
        assert resumed.last_question_version == 6
        assert resumed.last_answer_attempt == 1
        manager = LiveSessionManager(
            asr_sessions={}, answer_service=LiveAnswerService(answer), resume_state=resumed,
            queue_hook=save_queue, answer_hook=save_answer,
        )
        await manager.start()
        completions, detections = [], []
        for _ in range(100):
            event = await asyncio.wait_for(manager.next_event(), 1)
            if event.type == "question.detected":
                detections.append((event.payload["question_version"], event.payload["manual"]))
            if event.type == "answer.completed":
                completions.append((event.payload["question_version"], event.payload["attempt"]))
                if len(completions) == 3:
                    break
        assert completions == [(6, 2), (8, 1), (7, 1)]
        assert detections == [(8, True), (7, False)]
        # active version 已回到 7，新的题号仍从全局预留最大值 8 之后分配。
        await manager.handle(AnswerRequestEvent("manual", "全新问题"))
        assert (await event_matching(manager, "answer.completed")).payload["question_version"] == 9
        await manager.close("disconnect")
        assert not repository.resume_state(organization_id, actor_id, session.id).pending_answers

    try:
        asyncio.run(scenario())
    finally:
        with database.transaction() as connection:
            connection.execute(text("DELETE FROM career_assistant.live_interview_sessions WHERE id=:id"), {"id": session.id})
        database.close()
