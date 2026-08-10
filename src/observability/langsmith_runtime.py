"""Privacy-safe LangSmith tracing for model calls.

The platform processes resumes and interview notes. Raw prompts, model outputs,
uploaded files, and credentials must never be sent to an observability provider.
Only lifecycle metadata and provider-reported token usage are traced.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from typing import Any, TypeVar


T = TypeVar("T")
logger = logging.getLogger(__name__)


def is_langsmith_enabled() -> bool:
    """Return whether tracing has an API key and is explicitly enabled."""

    tracing = os.getenv("LANGSMITH_TRACING", os.getenv("LANGSMITH_TRACING_V2", "true"))
    return bool(os.getenv("LANGSMITH_API_KEY", "").strip()) and tracing.strip().lower() == "true"


def trace_llm_call(
    *,
    run_name: str,
    provider: str,
    model: str,
    message_count: int,
    input_characters: int,
    execute: Callable[[], T],
    summarize: Callable[[T], dict[str, Any]],
) -> T:
    """Execute one LLM request and record a metadata-only LangSmith trace.

    Tracing is best-effort: a LangSmith configuration or network problem must not
    block the user's actual model request.
    """

    if not is_langsmith_enabled():
        return execute()

    try:
        from langsmith import traceable
    except Exception as exc:  # 只降级观测能力，绝不重复业务模型调用。
        logger.warning("LangSmith 初始化失败，已跳过观测：%s", exc.__class__.__name__)
        return execute()

    metadata = {
        "provider": provider,
        "model": model,
        "privacy_mode": "metadata_only",
        "message_count": message_count,
        "input_characters": input_characters,
    }
    response_box: dict[str, T] = {}

    @traceable(
        name=run_name,
        run_type="llm",
        metadata=metadata,
        project_name=os.getenv("LANGSMITH_PROJECT", "ai-administration-platform"),
    )
    def _traced_call(trace_input: dict[str, Any]) -> dict[str, Any]:
        # Trace 输入始终是匿名摘要；业务异常由调用方正常处理，绝不重试整次调用。
        response = execute()
        response_box["value"] = response
        return summarize(response)

    _traced_call(
        {
            "provider": provider,
            "model": model,
            "message_count": message_count,
            "input_characters": input_characters,
            "content": "[hidden by privacy_mode=metadata_only]",
        }
    )
    return response_box["value"]
