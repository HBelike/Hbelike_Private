"""外部聊天的 PostgreSQL 仓储，身份与 LLM 历史完全分离。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import text

from src.career_assistant.external_chat import RETENTION_DAYS, ExternalChatSnapshot, utc_now
from src.career_assistant.persistence.database import CareerDatabase


ACCOUNT_FIELDS = "id, platform, platform_account_id, display_name, last_synced_at"
CONVERSATION_FIELDS = "id, account_id, platform_conversation_id, peer_id, peer_name, company, job_title, unread_count, last_message_at, last_message_preview, source_url, last_synced_at"


def _public(row) -> dict:
    return {key: str(value) if isinstance(value, UUID) else value.astimezone(UTC).isoformat().replace("+00:00", "Z") if isinstance(value, datetime) else value for key, value in row.items()}


class ExternalChatRepository:
    def __init__(self, database: CareerDatabase) -> None:
        self._database = database

    @staticmethod
    def _window(now: datetime | None) -> tuple[datetime, datetime]:
        now = (now or utc_now()).astimezone(UTC)
        return now, now - timedelta(days=RETENTION_DAYS)

    @staticmethod
    def _account(connection, organization_id: UUID, actor_id: UUID, account_id: UUID):
        row = connection.execute(text(f"""
            SELECT {ACCOUNT_FIELDS} FROM career_assistant.external_chat_accounts
            WHERE id = :id AND organization_id = :organization_id AND actor_id = :actor_id
        """), {"id": account_id, "organization_id": organization_id, "actor_id": actor_id}).mappings().one_or_none()
        if row is None:
            raise LookupError("外部聊天账号不存在或无访问权限")
        return row

    @staticmethod
    def _refresh_summaries(connection, now: datetime, cutoff: datetime, account_id: UUID | None = None) -> None:
        # 列表摘要可以先于正文取得，因此保留窗口内较新的源摘要，不能因尚未点开正文而清空。
        connection.execute(text("""
            WITH resolved AS (
                SELECT c.id,
                    CASE WHEN c.last_message_at BETWEEN :cutoff AND :now
                               AND (m.sent_at IS NULL OR c.last_message_at >= m.sent_at)
                         THEN c.last_message_at ELSE m.sent_at END AS last_message_at,
                    CASE WHEN c.last_message_at BETWEEN :cutoff AND :now
                               AND (m.sent_at IS NULL OR c.last_message_at >= m.sent_at)
                         THEN c.last_message_preview ELSE COALESCE(m.content_text, '') END AS preview,
                    CASE WHEN c.last_message_at BETWEEN :cutoff AND :now THEN c.unread_count ELSE NULL END AS unread_count
                FROM career_assistant.external_chat_conversations c
                LEFT JOIN LATERAL (
                    SELECT sent_at, content_text FROM career_assistant.external_chat_messages
                    WHERE conversation_id = c.id AND sent_at BETWEEN :cutoff AND :now
                    ORDER BY sent_at DESC, id DESC LIMIT 1
                ) m ON TRUE
                WHERE CAST(:account_id AS UUID) IS NULL OR c.account_id = CAST(:account_id AS UUID)
            )
            UPDATE career_assistant.external_chat_conversations c
            SET last_message_at = r.last_message_at, last_message_preview = r.preview,
                unread_count = r.unread_count
            FROM resolved r WHERE r.id = c.id
        """), {"now": now, "cutoff": cutoff, "account_id": account_id})

    def sync(self, organization_id: UUID, actor_id: UUID, snapshot: ExternalChatSnapshot) -> dict:
        now, cutoff = self._window(snapshot.observed_at)
        with self._database.transaction() as connection:
            account = connection.execute(text(f"""
                INSERT INTO career_assistant.external_chat_accounts
                    (id, organization_id, actor_id, platform, platform_account_id, display_name, last_synced_at)
                VALUES (:id, :organization_id, :actor_id, :platform, :platform_account_id, :display_name, :now)
                ON CONFLICT (organization_id, actor_id, platform, platform_account_id)
                DO UPDATE SET display_name = EXCLUDED.display_name, last_synced_at = EXCLUDED.last_synced_at
                RETURNING {ACCOUNT_FIELDS}
            """), {**snapshot.account, "id": uuid4(), "organization_id": organization_id, "actor_id": actor_id, "now": now}).mappings().one()
            account_id = account["id"]
            for item in snapshot.conversations:
                row = connection.execute(text("""
                    INSERT INTO career_assistant.external_chat_conversations AS current
                        (id, account_id, platform_conversation_id, peer_id, peer_name, company, job_title,
                         unread_count, last_message_at, last_message_preview, source_url, last_synced_at)
                    VALUES (:id, :account_id, :platform_conversation_id, :peer_id, :peer_name, :company,
                            :job_title, :unread_count, :last_message_at, :last_message_preview, :source_url, :now)
                    ON CONFLICT (account_id, platform_conversation_id) DO UPDATE SET
                        peer_name = EXCLUDED.peer_name, company = EXCLUDED.company, job_title = EXCLUDED.job_title,
                        unread_count = EXCLUDED.unread_count, source_url = EXCLUDED.source_url,
                        last_synced_at = EXCLUDED.last_synced_at,
                        last_message_preview = CASE WHEN EXCLUDED.last_message_at >= current.last_message_at OR current.last_message_at IS NULL
                                                    THEN EXCLUDED.last_message_preview ELSE current.last_message_preview END,
                        last_message_at = CASE WHEN EXCLUDED.last_message_at >= current.last_message_at OR current.last_message_at IS NULL
                                               THEN EXCLUDED.last_message_at ELSE current.last_message_at END
                    WHERE current.peer_id = EXCLUDED.peer_id
                    RETURNING id
                """), {**item, "id": uuid4(), "account_id": account_id, "now": now}).scalar_one_or_none()
                if row is None:
                    raise ValueError("平台会话对应的招聘者发生变化，请重新读取会话")
            conversations = {row["platform_conversation_id"]: row["id"] for row in connection.execute(text("""
                SELECT id, platform_conversation_id FROM career_assistant.external_chat_conversations WHERE account_id = :account_id
            """), {"account_id": account_id}).mappings()}
            for item in snapshot.messages:
                conversation_id = conversations.get(item["platform_conversation_id"])
                if conversation_id is None:
                    raise ValueError("消息引用的会话尚未同步，请先同步会话列表")
                row = connection.execute(text("""
                    INSERT INTO career_assistant.external_chat_messages AS current
                        (id, conversation_id, platform_message_id, sender_id, direction, message_type, content_text, sent_at, source_url, received_at)
                    VALUES (:id, :conversation_id, :platform_message_id, :sender_id, :direction, :message_type, :text, :sent_at, :source_url, :now)
                    ON CONFLICT (conversation_id, platform_message_id) DO UPDATE SET
                        content_text = EXCLUDED.content_text, message_type = EXCLUDED.message_type, source_url = EXCLUDED.source_url
                    WHERE current.sender_id = EXCLUDED.sender_id AND current.direction = EXCLUDED.direction AND current.sent_at = EXCLUDED.sent_at
                    RETURNING id
                """), {**item, "id": uuid4(), "conversation_id": conversation_id, "now": now}).scalar_one_or_none()
                if row is None:
                    raise ValueError("同一平台消息标识的发送者或原始时间不一致")
            self._refresh_summaries(connection, now, cutoff, account_id)
        return {"account": _public(account), "imported_conversations": len(snapshot.conversations), "imported_messages": len(snapshot.messages)}

    def list_accounts(self, organization_id: UUID, actor_id: UUID) -> list[dict]:
        with self._database.transaction() as connection:
            rows = connection.execute(text(f"""
                SELECT {ACCOUNT_FIELDS} FROM career_assistant.external_chat_accounts
                WHERE organization_id = :organization_id AND actor_id = :actor_id
                ORDER BY last_synced_at DESC, id
            """), {"organization_id": organization_id, "actor_id": actor_id}).mappings().all()
        return [_public(row) for row in rows]

    def list_conversations(self, organization_id: UUID, actor_id: UUID, account_id: UUID, *, now: datetime | None = None) -> list[dict]:
        now, cutoff = self._window(now)
        with self._database.transaction() as connection:
            self._account(connection, organization_id, actor_id, account_id)
            rows = connection.execute(text("""
                SELECT id, account_id, platform_conversation_id, peer_id, peer_name, company, job_title,
                    CASE WHEN last_message_at BETWEEN :cutoff AND :now THEN unread_count ELSE NULL END AS unread_count,
                    CASE WHEN last_message_at BETWEEN :cutoff AND :now THEN last_message_at ELSE NULL END AS last_message_at,
                    CASE WHEN last_message_at BETWEEN :cutoff AND :now THEN last_message_preview ELSE '' END AS last_message_preview,
                    source_url, last_synced_at
                FROM career_assistant.external_chat_conversations WHERE account_id = :account_id
                ORDER BY (CASE WHEN last_message_at BETWEEN :cutoff AND :now THEN last_message_at ELSE NULL END) DESC NULLS LAST, id
            """), {"account_id": account_id, "now": now, "cutoff": cutoff}).mappings().all()
        return [_public(row) for row in rows]

    def list_messages(self, organization_id: UUID, actor_id: UUID, conversation_id: UUID, *, limit: int = 100, before: UUID | None = None, now: datetime | None = None) -> dict:
        if not 1 <= limit <= 200:
            raise ValueError("消息分页数量应在 1 至 200 之间")
        now, cutoff = self._window(now)
        with self._database.transaction() as connection:
            conversation = connection.execute(text("""
                SELECT c.id, c.platform_conversation_id FROM career_assistant.external_chat_conversations c
                JOIN career_assistant.external_chat_accounts a ON a.id = c.account_id
                WHERE c.id = :id AND a.organization_id = :organization_id AND a.actor_id = :actor_id
            """), {"id": conversation_id, "organization_id": organization_id, "actor_id": actor_id}).mappings().one_or_none()
            if conversation is None:
                raise LookupError("外部聊天会话不存在或无访问权限")
            parameters = {"conversation_id": conversation_id, "now": now, "cutoff": cutoff, "limit": limit + 1}
            cursor_clause = ""
            if before is not None:
                cursor = connection.execute(text("""
                    SELECT id, sent_at FROM career_assistant.external_chat_messages
                    WHERE id = :id AND conversation_id = :conversation_id AND sent_at BETWEEN :cutoff AND :now
                """), {**parameters, "id": before}).mappings().one_or_none()
                if cursor is None:
                    raise LookupError("消息分页位置已过期或不存在，请刷新会话")
                cursor_clause = "AND (sent_at, id) < (:before_time, :before_id)"
                parameters.update(before_time=cursor["sent_at"], before_id=cursor["id"])
            rows = connection.execute(text(f"""
                SELECT id, conversation_id, platform_message_id, sender_id, direction, message_type,
                       content_text AS text, sent_at, source_url
                FROM career_assistant.external_chat_messages
                WHERE conversation_id = :conversation_id AND sent_at BETWEEN :cutoff AND :now
                {cursor_clause} ORDER BY sent_at DESC, id DESC LIMIT :limit
            """), parameters).mappings().all()
            has_more = len(rows) > limit
            messages = [{**_public(row), "platform_conversation_id": conversation["platform_conversation_id"]} for row in reversed(rows[:limit])]
        return {"messages": messages, "has_more": has_more}

    def purge_expired(self, *, now: datetime | None = None) -> int:
        now, cutoff = self._window(now)
        with self._database.transaction() as connection:
            deleted = connection.execute(text("DELETE FROM career_assistant.external_chat_messages WHERE sent_at < :cutoff"), {"cutoff": cutoff}).rowcount
            self._refresh_summaries(connection, now, cutoff)
        return deleted
