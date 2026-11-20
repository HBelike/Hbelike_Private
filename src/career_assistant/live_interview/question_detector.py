"""面试官 final 话语的低延迟问题检测与追问分类。"""

from __future__ import annotations

import time
from collections.abc import Callable
import re

from src.career_assistant.live_interview.contracts import (
    DetectedQuestion,
    QuestionIntent,
    SpeakerRole,
    TranscriptEvent,
)


_FILLER_UTTERANCES = frozenset(
    {
        "嗯",
        "嗯嗯",
        "哦",
        "噢",
        "啊",
        "好的",
        "好",
        "行",
        "可以",
        "知道了",
        "明白了",
        "okay",
        "ok",
    }
)
_EDGE_PUNCTUATION = re.compile(r"^[\s，。！？!?、,.；;：:~～…]+|[\s，。！？!?、,.；;：:~～…]+$")
_DUPLICATE_WINDOW_SECONDS = 3.0
_FOLLOW_UP = re.compile(
    r"刚才|前面提到|上一个|上一种|除此之外|那如果|那么|进一步|继续|具体呢|还有呢|"
    r"它(?:[们的有在是比能？?。]|$)|这(?:个|种)方案|那个方案|"
    r"^(?:那|它|这个|那个|这种|这样|上述|为什么[呢啊？?。]*$|展开[说讲一下呢？?。]*$)|"
    r"^(?:优缺点|优点|缺点|好处|坏处|局限(?:性)?|代价|还有(?:别的|其他)(?:办法|方案|选择))"
    r"(?:呢|吗|有(?:哪些|什么)|是(?:哪些|什么)|分别是什么|[？?])|"
    r"^(?:你)?(?:(?:能不能|能否|能|可以|可否|请|麻烦)(?:你)?)?(?:再)?"
    r"举(?:一个|个)(?:(?:更)?具体的?)?例子(?:呢|吗|吧|么)?[。！？?!…\s]*$|"
    r"\b(?:you (?:just )?mentioned|that (?:approach|design)|its (?:limits|drawbacks)|"
    r"what about|why exactly)\b|^why[?!.\s]*$|\b(?:does|would|could|is) (?:it|that)\b",
    re.IGNORECASE,
)
_QUESTION = re.compile(
    r"[?？]|吗[。！!…\s]*$|呢[。！!…\s]*$|为什么|怎么(?:样|办)?|如何|能否|是否|"
    r"有什么|是什么|什么是|有哪些|哪(?:些|个|种)|多少|能不能|可不可以|你觉得|你认为|"
    r"(?:^|[，。；、,]\s*|请|能不能|可以|你来|你能|你会|麻烦|重点)"
    r"(?:分析|评价|比较|解释|描述|设计|介绍|[说谈讲聊]一下|说说|谈谈|讲讲|聊聊|说明|举例|举一?个|展开|实现|写出|证明|推导|计算|演示|列举)|"
    r"请(?:你|结合|给出|回答|分享|用|看|写|继续|说|讲|谈)|"
    r"^(?:what|why|how|when|which|who|where|can|could|would|do|does|is|are|"
    r"explain|describe|design|compare|implement|discuss|tell me|walk me through|give me)\b",
    re.IGNORECASE,
)


class RuleBasedQuestionDetector:
    """确定性首层检测器；接口可替换为带结构化 LLM 的复合检测器。"""

    def __init__(self, *, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._last_text: str | None = None
        self._last_detected_at: float | None = None
        self._background = ""

    def take_background(self) -> str:
        text, self._background = self._background, ""
        return text

    def restore_background(self, conversation: tuple[str, ...], last_question: str | None) -> None:
        """从最近转写取回断开前尚未形成问题的连续题干。"""
        for line in conversation:
            role, _, text = line.partition(": ")
            if not text or _EDGE_PUNCTUATION.sub("", text.casefold()) in _FILLER_UTTERANCES:
                continue
            if role == "interviewer" and not _QUESTION.search(text) and not (
                last_question and text in last_question.splitlines()
            ):
                self._background = (self._background + "\n" + text).strip()[-4_000:]
            else:
                self.take_background()

    def detect(
        self,
        event: TranscriptEvent,
        previous_question: str | None = None,
    ) -> DetectedQuestion | None:
        if not event.is_final:
            return None
        text = re.sub(r"\s+", " ", event.text).strip()
        lowered = text.casefold()
        filler_key = _EDGE_PUNCTUATION.sub("", lowered)
        if not text or filler_key in _FILLER_UTTERANCES:
            return None
        if event.role is not SpeakerRole.INTERVIEWER:
            # 发言轮次已经切换，避免把上个话题的铺垫并入下一题。
            self.take_background()
            return None
        intent = self.classify(text, previous_question)
        is_follow_up = intent is QuestionIntent.FOLLOW_UP
        if not _QUESTION.search(text) and not (is_follow_up and text.startswith(("继续", "进一步", "展开"))):
            # 保留最近的题干和约束；纯陈述也会原样出现在字幕，可手动触发。
            self._background = (self._background + "\n" + text).strip()[-4_000:]
            return None
        background = self.take_background()
        question = f"{background}\n{text}" if background else text
        now = self._clock()
        if (
            not background
            and self._last_text == lowered
            and self._last_detected_at is not None
            and now - self._last_detected_at <= _DUPLICATE_WINDOW_SECONDS
        ):
            return None
        self._last_text = lowered
        self._last_detected_at = now
        return DetectedQuestion(
            normalized_question=question,
            intent=intent,
            confidence=1.0,
            is_follow_up=is_follow_up,
        )

    @staticmethod
    def classify(text: str, previous_question: str | None) -> QuestionIntent:
        text = text.casefold()
        if previous_question and _FOLLOW_UP.search(text):
            return QuestionIntent.FOLLOW_UP
        if any(marker in text for marker in ("项目", "经历", "负责", "你做", "your project")):
            return QuestionIntent.PROJECT_DEEP_DIVE
        if any(marker in text for marker in ("系统设计", "架构", "design a", "design the")):
            return QuestionIntent.SYSTEM_DESIGN
        if any(marker in text for marker in ("算法", "代码", "复杂度", "algorithm", "coding")):
            return QuestionIntent.CODING_OR_ALGORITHM
        if any(marker in text for marker in ("如果", "场景", "case", "scenario")):
            return QuestionIntent.CASE_OR_SCENARIO
        if any(marker in text for marker in ("冲突", "失败", "挑战", "行为", "behavioral")):
            return QuestionIntent.BEHAVIORAL
        return QuestionIntent.KNOWLEDGE
