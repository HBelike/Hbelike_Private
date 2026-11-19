"""对话求职卡片数据契约：保存有限岗位摘要与 JD 摘录，不保存定位坐标。"""

from __future__ import annotations

from copy import deepcopy

CARD_STATUSES = frozenset({"editing", "searched", "selected", "dismissed"})
CITY_SOURCES = frozenset({"user", "profile", "location", "manual"})
JOB_SUMMARY_LIMITS = {"id": 8192, "title": 240, "company": 240, "salary": 120, "city": 120,
                      "experience": 120, "education": 120, "skills": 600, "description_excerpt": 2000}
JOB_SUMMARY_LABELS = {"id": "岗位标识", "title": "岗位标题", "company": "公司名称", "salary": "薪资", "city": "工作城市",
                      "experience": "经验要求", "education": "学历要求", "skills": "岗位技能", "description_excerpt": "岗位描述摘录"}


def card_message_payload(message) -> dict:
    """实时更新与历史使用相同的卡片消息字段。"""
    return {
        "id": str(message.id), "turn_id": str(message.turn_id) if message.turn_id else None,
        "role": message.role.value, "content": message.content_text,
        "is_redacted": message.is_redacted, "created_at": message.created_at.isoformat(),
        "ui_payload": message.ui_payload,
    }


def _short_text(value: object, maximum: int = 120, *, label: str = "搜索条件") -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label}必须是文本")
    normalized = value.strip()
    if len(normalized) > maximum:
        raise ValueError(f"{label}不能超过 {maximum} 个字符")
    return normalized


def _job_summary(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise ValueError("岗位摘要格式无效")
    allowed = set(JOB_SUMMARY_LIMITS)
    if set(value) - allowed:
        raise ValueError("岗位摘要包含不支持的字段")
    # BOSS securityId 是较长的不透明标识，不能使用展示文字的长度限制或截断。
    return {key: _short_text(item, JOB_SUMMARY_LIMITS[key], label=JOB_SUMMARY_LABELS[key])
            for key, item in value.items()}


def patch_job_search_card(current: dict, patch: dict) -> dict:
    """仅合并业务需要的字段，历史卡片不接受任意 UI 或代码。"""
    if current.get("type") != "job_search" or current.get("version") not in {1, 2}:
        raise ValueError("该消息不是有效的职位搜索卡片")
    if current.get("version") == 2 and (not isinstance(patch, dict) or set(patch) - {"status", "selected_job"}):
        raise ValueError("新版卡片仅更新展示与确认状态，岗位数据请保存到搜索批次")
    if not isinstance(patch, dict) or set(patch) - {
        "status", "conditions", "result_summary", "selected_job", "auto_search",
    }:
        raise ValueError("卡片更新字段无效")
    result = deepcopy(current)
    if "auto_search" in patch:
        if type(patch["auto_search"]) is not bool:
            raise ValueError("自动搜索标记无效")
        result["auto_search"] = patch["auto_search"]
    if "status" in patch:
        if not isinstance(patch["status"], str) or patch["status"] not in CARD_STATUSES:
            raise ValueError("卡片状态无效")
        result["status"] = patch["status"]
    if "conditions" in patch:
        conditions = patch["conditions"]
        if not isinstance(conditions, dict) or set(conditions) - {"keyword", "city", "city_source"}:
            raise ValueError("搜索条件无效")
        merged = dict(result.get("conditions", {}))
        for field in ("keyword", "city"):
            if field in conditions:
                value = conditions[field]
                merged[field] = None if value is None and field == "city" else _short_text(value)
        if "city_source" in conditions:
            source = conditions["city_source"]
            if source is not None and (not isinstance(source, str) or source not in CITY_SOURCES):
                raise ValueError("城市来源无效")
            merged["city_source"] = source
        result["conditions"] = merged
    if "result_summary" in patch:
        summary = patch["result_summary"]
        if summary is None:
            result["result_summary"] = None
        else:
            if not isinstance(summary, dict) or set(summary) != {"count", "jobs"}:
                raise ValueError("搜索结果摘要无效")
            count, jobs = summary["count"], summary["jobs"]
            if type(count) is not int or not 0 <= count <= 100_000:
                raise ValueError("岗位数量无效")
            if not isinstance(jobs, list) or len(jobs) > 10:
                raise ValueError("岗位摘要最多保存十条")
            result["result_summary"] = {"count": count, "jobs": [_job_summary(job) for job in jobs]}
    if "selected_job" in patch:
        value = patch["selected_job"]
        result["selected_job"] = None if value is None else _job_summary(value)
    return result


def new_job_search_card(keyword: str | None, city: str | None, *, auto_search: bool) -> dict:
    return {
        "type": "job_search", "version": 1, "status": "editing",
        "conditions": {"keyword": keyword or "", "city": city, "city_source": "user" if city else None},
        "auto_search": auto_search,
        "result_summary": None, "selected_job": None,
    }
