from __future__ import annotations

import re


DEFAULT_WECHAT_TITLE_MAX_CHARS = 28


def compact_wechat_title(title: str, max_chars: int = DEFAULT_WECHAT_TITLE_MAX_CHARS) -> str:
    """生成适合公众号草稿标题栏展示的紧凑标题。

    正文标题保持完整；该函数仅供公众号草稿标题和审核台预览使用，避免
    64 字符的长标题在公众号编辑器中显得拥挤或被截断得难以理解。
    """

    normalized = re.sub(r"\s+", " ", str(title or "")).strip()
    if not normalized:
        return "GitHub 技术周报"

    try:
        limit = int(max_chars)
    except (TypeError, ValueError):
        limit = DEFAULT_WECHAT_TITLE_MAX_CHARS
    limit = max(8, limit)

    if len(normalized) <= limit:
        return normalized

    # 尽量在自然分隔符处截断，避免标题以半句话结束。
    candidate = normalized[:limit]
    for separator in ("｜", "—", "-", "，", "。"):
        separator_index = candidate.rfind(separator)
        if separator_index >= max(6, limit // 3):
            candidate = candidate[:separator_index]
            break

    candidate = candidate.rstrip("：:｜—-，。；、 ")
    if not candidate:
        candidate = normalized[: limit - 1].rstrip()
    return f"{candidate}…"
