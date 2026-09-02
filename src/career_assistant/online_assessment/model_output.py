"""模型 JSON 输出的严格解析工具。"""

from __future__ import annotations

import json
import re

from pydantic import ValidationError


class OnlineAssessmentModelOutputError(ValueError):
    """模型没有返回可校验结构时的领域错误。"""


def normalize_solution_payload(payload: dict[str, object]) -> dict[str, object]:
    """只修正常见的无歧义形态，不替模型猜测缺失业务字段。"""

    normalized = dict(payload)
    assumptions = normalized.get("assumptions")
    if assumptions is None:
        normalized["assumptions"] = []
    elif isinstance(assumptions, str):
        normalized["assumptions"] = [assumptions]
    return normalized


def validation_error_summary(error: ValidationError) -> tuple[str, ...]:
    """生成不包含模型原始值的字段级纠错提示。"""

    summaries: list[str] = []
    for item in error.errors(include_input=False, include_url=False):
        path = ".".join(str(part) for part in item.get("loc", ())) or "root"
        summaries.append(f"{path}:{item.get('type', 'invalid')}")
    return tuple(summaries)


def parse_json_object(raw: str) -> dict[str, object]:
    """接受纯 JSON 或单个 Markdown JSON 围栏，拒绝数组与尾随说明。"""

    content = raw.strip()
    fence = re.fullmatch(r"```(?:json)?\s*(\{.*\})\s*```", content, flags=re.DOTALL | re.IGNORECASE)
    if fence:
        content = fence.group(1)
    try:
        value = json.loads(content)
    except json.JSONDecodeError as exc:
        raise OnlineAssessmentModelOutputError("模型没有返回有效 JSON") from exc
    if not isinstance(value, dict):
        raise OnlineAssessmentModelOutputError("模型输出必须是 JSON 对象")
    return value
