"""面试官 final 话语的低延迟问题检测与追问分类。"""

from __future__ import annotations

import re

from src.career_assistant.live_interview.contracts import (
    DetectedQuestion,
    QuestionIntent,
    SpeakerRole,
    TranscriptEvent,
)


_QUESTION_MARKERS = (
    "？",
    "?",
    "请介绍",
    "请说明",
    "请解释",
    "请设计",
    "谈谈",
    "说说",
    "如何",
    "为什么",
    "什么",
    "怎么",
    "是否",
    "能否",
    "describe",
    "explain",
    "design",
    "how ",
    "why ",
    "what ",
    "tell me",
)
_FOLLOW_UP = ("那如果", "进一步", "继续", "刚才", "那么", "除此之外", "具体呢", "why exactly")


class RuleBasedQuestionDetector:
    """确定性首层检测器；接口可替换为带结构化 LLM 的复合检测器。"""

    def detect(
        self,
        event: TranscriptEvent,
        previous_question: str | None = None,
    ) -> DetectedQuestion | None:
        if event.role is not SpeakerRole.INTERVIEWER or not event.is_final:
            return None
        text = re.sub(r"\s+", " ", event.text).strip()
        lowered = text.casefold()
        if len(text) < 3 or not any(marker.casefold() in lowered for marker in _QUESTION_MARKERS):
            return None
        is_follow_up = previous_question is not None and any(
            marker.casefold() in lowered for marker in _FOLLOW_UP
        )
        intent = QuestionIntent.FOLLOW_UP if is_follow_up else self._classify(lowered)
        return DetectedQuestion(
            normalized_question=text,
            intent=intent,
            confidence=0.94 if text.endswith(("?", "？")) else 0.86,
            is_follow_up=is_follow_up,
        )

    @staticmethod
    def _classify(text: str) -> QuestionIntent:
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
