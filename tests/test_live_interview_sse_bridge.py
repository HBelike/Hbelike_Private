"""验证真实回答服务的同步 SSE 桥关闭与取消。"""

import asyncio
from threading import Event

import pytest

from src.career_assistant.live_interview import web as live_web
from src.career_assistant.live_interview.answer_service import LiveAnswerContext, LiveAnswerService
from src.career_assistant.live_interview.contracts import QuestionIntent


def answer_stream(source):
    service = LiveAnswerService(lambda _: live_web._iterate_answer_stream(source))
    return service.stream("解释限流", QuestionIntent.KNOWLEDGE, LiveAnswerContext())


def test_sse_bridge_first_chunk_does_not_prefetch_and_explicit_close_releases_source():
    async def scenario():
        closed = Event()
        second_read = Event()

        def source():
            try:
                yield "首字"
                second_read.set()
                yield "后续"
            finally:
                closed.set()

        upstream = source()
        stream = answer_stream(upstream)
        assert await asyncio.wait_for(anext(stream), timeout=0.5) == "首字"
        assert not second_read.is_set()
        await asyncio.wait_for(stream.aclose(), timeout=0.5)
        # 保留 upstream 引用，确保关闭发生在 finally，而非引用计数或 GC。
        assert closed.is_set()
        assert upstream.gi_frame is None

    asyncio.run(scenario())


@pytest.mark.parametrize("already_yielded", [False, True])
def test_sse_bridge_cancel_returns_before_blocked_read_and_closes_when_read_finishes(already_yielded):
    async def scenario():
        entered = Event()
        release = Event()
        closed = Event()

        def source():
            try:
                if already_yielded:
                    yield "首字"
                entered.set()
                if not release.wait(timeout=3):
                    raise TimeoutError("测试未释放 SSE 读取")
                yield "迟到片段"
            finally:
                closed.set()

        upstream = source()
        stream = answer_stream(upstream)
        pending = None
        try:
            if already_yielded:
                assert await anext(stream) == "首字"
            pending = asyncio.create_task(anext(stream))
            assert await asyncio.to_thread(entered.wait, 1)
            pending.cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(pending, timeout=0.5)
            assert not closed.is_set()
            release.set()
            assert await asyncio.to_thread(closed.wait, 1)
            assert upstream.gi_frame is None
        finally:
            release.set()
            if pending is not None:
                await asyncio.gather(pending, return_exceptions=True)
            await stream.aclose()

    asyncio.run(scenario())


def test_sse_bridge_complete_and_failure_release_source_without_changing_text_or_error():
    async def scenario():
        closed = Event()

        def complete():
            try:
                yield "结论"
                yield "要点"
                yield "尾段"
            finally:
                closed.set()

        upstream = complete()
        assert "".join([chunk async for chunk in answer_stream(upstream)]) == "结论要点尾段"
        assert closed.is_set()

        closed.clear()

        def failed():
            try:
                yield "首字"
                raise RuntimeError("上游中断")
            finally:
                closed.set()

        upstream = failed()
        stream = answer_stream(upstream)
        assert await anext(stream) == "首字"
        with pytest.raises(RuntimeError, match="上游中断"):
            await anext(stream)
        assert closed.is_set()
        assert upstream.gi_frame is None

    asyncio.run(scenario())
