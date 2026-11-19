"""外部招聘聊天的受约束快照，不进入 LLM 会话历史。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


RETENTION_DAYS = 30
MEDIA_PLACEHOLDERS = {"image": "[图片]", "file": "[附件]", "audio": "[语音]", "unknown": "[未知消息]"}


def utc_now() -> datetime:
    return datetime.now(UTC)


def source_time(value: object, *, optional: bool = False) -> datetime | None:
    """源时间必须有时区，不将缺失或本地时间冒充为刚收到。"""

    if value is None or value == "":
        if optional:
            return None
        raise ValueError("消息缺少可靠的原始时间")
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("消息原始时间格式无效") from exc
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("消息原始时间必须包含时区")
    return value.astimezone(UTC)


def _text(value: object, name: str, maximum: int, *, required: bool = False) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name}必须是文本")
    value = value.strip()
    if required and not value:
        raise ValueError(f"{name}不能为空")
    if len(value) > maximum:
        raise ValueError(f"{name}超过允许长度")
    return value


def _fields(value: object, allowed: set[str], name: str) -> dict:
    if not isinstance(value, dict) or set(value) - allowed:
        raise ValueError(f"{name}格式无效或包含不支持的字段")
    return value


def _source_url(value: object) -> str:
    value = _text(value, "来源链接", 2000)
    if not value:
        return ""
    parsed = urlsplit(value)
    if parsed.scheme != "https" or parsed.hostname != "www.zhipin.com" or parsed.username or parsed.password:
        raise ValueError("聊天来源必须是 BOSS 官方 HTTPS 页面")
    # 运行时 securityId、Token 等只在浏览器使用，历史中只保留稳定跳转参数。
    query = urlencode([(key, item) for key, item in parse_qsl(parsed.query) if key in {"id", "jobId"}])
    return urlunsplit(("https", "www.zhipin.com", parsed.path, query, ""))


@dataclass(frozen=True)
class ExternalChatSnapshot:
    account: dict
    conversations: tuple[dict, ...]
    messages: tuple[dict, ...]
    observed_at: datetime


def normalize_snapshot(value: object, *, now: datetime | None = None) -> ExternalChatSnapshot:
    """限制字段、媒体内容和源时间，过期消息不会因重新导入获得新寿命。"""

    now = (now or utc_now()).astimezone(UTC)
    cutoff = now - timedelta(days=RETENTION_DAYS)
    payload = _fields(value, {"account", "conversations", "messages"}, "同步快照")
    account = _fields(payload.get("account"), {"platform", "platform_account_id", "display_name"}, "平台账号")
    if account.get("platform") != "boss":
        raise ValueError("当前只支持 BOSS 入站聊天")
    normalized_account = {
        "platform": "boss",
        "platform_account_id": _text(account.get("platform_account_id"), "平台账号标识", 500, required=True),
        "display_name": _text(account.get("display_name", ""), "平台账号名称", 240),
    }
    raw_conversations = payload.get("conversations", [])
    raw_messages = payload.get("messages", [])
    if not isinstance(raw_conversations, list) or len(raw_conversations) > 500:
        raise ValueError("一次最多同步 500 个会话")
    if not isinstance(raw_messages, list) or len(raw_messages) > 1000:
        raise ValueError("一次最多同步 1000 条消息")
    conversations: dict[str, dict] = {}
    for raw in raw_conversations:
        item = _fields(raw, {"platform_conversation_id", "peer_id", "peer_name", "company", "job_title", "unread_count", "last_message_at", "last_message_preview", "source_url"}, "聊天会话")
        key = _text(item.get("platform_conversation_id"), "平台会话标识", 500, required=True)
        sent_at = source_time(item.get("last_message_at"), optional=True)
        visible_summary = sent_at is not None and cutoff <= sent_at <= now
        unread = item.get("unread_count")
        if unread is not None and (isinstance(unread, bool) or not isinstance(unread, int) or unread < 0):
            raise ValueError("未读数必须是非负整数或空值")
        conversations[key] = {
            "platform_conversation_id": key,
            "peer_id": _text(item.get("peer_id"), "对方标识", 500, required=True),
            "peer_name": _text(item.get("peer_name", ""), "对方名称", 240),
            "company": _text(item.get("company", ""), "公司", 240),
            "job_title": _text(item.get("job_title", ""), "岗位", 240),
            "unread_count": unread if visible_summary else None,
            "last_message_at": sent_at if visible_summary else None,
            "last_message_preview": _text(item.get("last_message_preview", ""), "消息摘要", 2000) if visible_summary else "",
            "source_url": _source_url(item.get("source_url", "")),
        }
    messages: dict[tuple[str, str], dict] = {}
    for raw in raw_messages:
        item = _fields(raw, {"platform_conversation_id", "platform_message_id", "sender_id", "direction", "message_type", "text", "sent_at", "source_url"}, "聊天消息")
        key = _text(item.get("platform_conversation_id"), "平台会话标识", 500, required=True)
        message_id = _text(item.get("platform_message_id"), "平台消息标识", 500, required=True)
        sent_at = source_time(item.get("sent_at"))
        direction = item.get("direction")
        message_type = item.get("message_type")
        if direction not in {"incoming", "outgoing"}:
            raise ValueError("消息方向无效")
        if message_type not in {"text", *MEDIA_PLACEHOLDERS}:
            raise ValueError("消息类型无效")
        body = item.get("text", "") if message_type == "text" else MEDIA_PLACEHOLDERS[message_type]
        if not isinstance(body, str) or len(body) > 30000:
            raise ValueError("消息正文必须是最多 30000 字的文本")
        normalized = {
            "platform_conversation_id": key,
            "platform_message_id": message_id,
            "sender_id": _text(item.get("sender_id"), "发送者标识", 500, required=True),
            "direction": direction,
            "message_type": message_type,
            "text": body,
            "sent_at": sent_at,
            "source_url": _source_url(item.get("source_url", "")),
        }
        if cutoff <= sent_at <= now:
            messages[(key, message_id)] = normalized
    return ExternalChatSnapshot(normalized_account, tuple(conversations.values()), tuple(messages.values()), now)
