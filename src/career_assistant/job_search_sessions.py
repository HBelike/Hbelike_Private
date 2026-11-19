"""对话求职批次的有限数据契约。"""
import hashlib
import json


def text_field(value, maximum, name):
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{name}必须是文本")
    value = value.strip()
    if len(value) > maximum:
        raise ValueError(f"{name}不能超过 {maximum} 个字符")
    return value


def normalize_conditions(value):
    if not isinstance(value, dict):
        raise ValueError("搜索条件无效")
    source = value.get("city_source")
    if source not in (None, "user", "manual", "profile", "location"):
        raise ValueError("城市来源无效")
    return {"keyword": text_field(value.get("keyword"), 120, "关键词"),
            "city": text_field(value.get("city"), 120, "城市") or None, "city_source": source}


def normalize_job(value):
    if not isinstance(value, dict):
        raise ValueError("岗位必须是对象")
    key = value.get("jobId") or value.get("bridge_key") or value.get("id") or value.get("securityId")
    key = text_field(str(key) if key is not None else "", 8192, "岗位标识")
    if not key:
        raise ValueError("岗位缺少标识")
    result = {"bridge_key": key}
    aliases = {"company": value.get("company") or value.get("companyShort"),
               "education": value.get("education") or value.get("degree")}
    limits = {"title": 240, "company": 240, "salary": 120, "city": 120, "experience": 120,
              "education": 120, "description": 30000, "description_excerpt": 2000,
              "fetchedAt": 120, "jobId": 8192, "securityId": 8192, "lid": 8192, "id": 8192,
              "url": 2000, "detail_error": 1000}
    for field, maximum in limits.items():
        if field in value or field in aliases:
            result[field] = text_field(aliases.get(field, value.get(field)), maximum, field)
    for field in ("skills", "welfare"):
        items = value.get(field, [])
        if isinstance(items, str):
            items = [items]
        if not isinstance(items, list) or len(items) > 100:
            raise ValueError(f"{field}格式无效")
        result[field] = [text_field(item, 240, field) for item in items]
    status = value.get("detail_status", "ready" if result.get("description") else "not_requested")
    if status not in ("not_requested", "loading", "ready", "failed"):
        raise ValueError("详情状态无效")
    result["detail_status"] = status
    return result


def operation_digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
