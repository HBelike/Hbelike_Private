"""统一中文、受个人事实约束的实时回答流。"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable

from src.career_assistant.live_interview.context_builder import LiveAnswerContext
from src.career_assistant.live_interview.contracts import QuestionIntent


PromptStreamer = Callable[[str], AsyncIterator[str]]


def build_answer_prompt(
    question: str,
    intent: QuestionIntent,
    context: LiveAnswerContext,
) -> str:
    conversation = "\n".join(context.recent_conversation) or "（暂无）"
    evidence = "\n".join(f"- {item}" for item in context.interview_evidence) or "（暂无）"
    terms = "、".join(context.terminology) or "（暂无）"
    candidate_facts = context.candidate_facts or "（暂无已确认个人材料）"
    target_role = context.target_role or "（未绑定目标岗位）"
    return f"""你是实时面试回答助手。请立即给出可扫读的回答建议。

硬性规则：
1. 回答统一使用中文；技术、医疗、金融、法律、制造、教育等领域的专有名词保留原文。
2. 个人经历、职责、时间、业绩、规模和数字只能引用“已确认个人材料”，不得编造。
3. 若个人材料不足，只给出可表达思路，并明确写“请替换为真实经历”，不能把面经中他人的经历当成用户经历。
4. 先输出“直接结论”和 3～5 个短要点，再给表达示例、事实证据与可能追问；避免冗长铺垫。

问题类型：{intent.value}
面试官问题：{question.strip()}

最近对话：
{conversation}

已确认个人材料：
{candidate_facts}

目标岗位：
{target_role}

面经检索证据（仅作为题型参考，不代表用户经历）：
{evidence}

必须保留原文的术语：{terms}
"""


class LiveAnswerService:
    def __init__(self, prompt_streamer: PromptStreamer) -> None:
        self._prompt_streamer = prompt_streamer

    async def stream(
        self,
        question: str,
        intent: QuestionIntent,
        context: LiveAnswerContext,
    ) -> AsyncIterator[str]:
        prompt = build_answer_prompt(question, intent, context)
        async for chunk in self._prompt_streamer(prompt):
            if chunk:
                yield chunk


__all__ = ["LiveAnswerContext", "LiveAnswerService", "build_answer_prompt"]
