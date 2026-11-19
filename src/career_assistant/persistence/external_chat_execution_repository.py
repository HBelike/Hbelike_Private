"""外部聊天单活动执行端及发送意图，使用账号行锁协调条件更新。"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import text

from src.career_assistant.external_chat import RETENTION_DAYS, utc_now
from src.career_assistant.persistence.database import CareerDatabase
from src.career_assistant.persistence.external_chat_repository import _public


LEASE_SECONDS = 90
OPERATION_SECONDS = 30
OPERATION_FIELDS = "client_operation_id, account_id, conversation_id, executor_id, revision, content_text AS text, status, created_at, expires_at, retain_until, platform_message_id, error, cancel_requested"
STATUSES = {"pending", "executing", "submitting", "submitted", "accepted", "failed", "unknown", "cancelled", "expired"}


class ExternalChatExecutionError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code


def _now(value: datetime | None) -> datetime:
    return (value or utc_now()).astimezone(UTC)


def _string(value, name: str, *, limit: int = 30000) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError(f"{name}必须是非空文本且不超过 {limit} 字")
    return value


def _business_rejection(error) -> bool:
    """区分适配器报告的业务拒绝与普通超时；此格式不是平台签名凭据。"""

    if not isinstance(error, str):
        return False
    parts = error.split(":", 2)
    return (len(parts) == 3 and parts[0] == "platform_rejected"
            and bool(parts[1].strip()) and bool(parts[2].strip())
            and parts[1].strip().lower() not in {"timeout", "network", "unknown", "request_timeout", "network_error"})


class ExternalChatExecutionRepository:
    def __init__(self, database: CareerDatabase) -> None:
        self._database = database

    @staticmethod
    def _account(connection, organization_id: UUID, actor_id: UUID, account_id: UUID):
        row = connection.execute(text("""
            SELECT * FROM career_assistant.external_chat_accounts
            WHERE id = :id AND organization_id = :org AND actor_id = :actor FOR UPDATE
        """), {"id": account_id, "org": organization_id, "actor": actor_id}).mappings().one_or_none()
        if row is None:
            raise LookupError("外部聊天账号不存在或无访问权限")
        return row

    @staticmethod
    def _execution(account) -> dict:
        return _public({"executor_id": account["executor_id"], "revision": account["execution_revision"],
                        "expires_at": account["execution_expires_at"], "paused": account["paused"],
                        "pause_code": account["pause_code"], "pause_reason": account["pause_reason"]})

    @staticmethod
    def _require_owner(account, executor_id: str, revision: int, now: datetime, *, allow_paused=False) -> None:
        if (account["executor_id"] != executor_id or account["execution_revision"] != revision
                or account["execution_expires_at"] is None or account["execution_expires_at"] <= now):
            raise ExternalChatExecutionError("execution_lost", "当前页面的活动执行资格已失效，请重新申请或接管")
        if account["paused"] and not allow_paused:
            raise ExternalChatExecutionError("account_paused", account["pause_reason"] or "当前 BOSS 账号已暂停，请处理后手动继续")

    @staticmethod
    def _expire(connection, account_id: UUID, now: datetime) -> None:
        # 已领取操作失联只能进入待核对，不能把可能发生的提交显示成已取消。
        connection.execute(text("""
            UPDATE career_assistant.external_chat_operations SET
                status = CASE WHEN status = 'pending' THEN 'expired' ELSE 'unknown' END,
                cancel_requested = TRUE, error = 'operation_expired'
            WHERE account_id = :account AND expires_at <= :now AND status IN ('pending','executing','submitting')
        """), {"account": account_id, "now": now})

    @staticmethod
    def _invalidate_unsubmitted(connection, account_id: UUID, reason: str) -> None:
        connection.execute(text("""
            UPDATE career_assistant.external_chat_operations SET
                status = CASE WHEN status = 'pending' THEN 'cancelled' ELSE 'unknown' END,
                cancel_requested = TRUE, error = :reason
            WHERE account_id = :account AND status IN ('pending','executing','submitting')
        """), {"account": account_id, "reason": reason})

    @staticmethod
    def _has_unsettled_submission(connection, account_id: UUID, now: datetime) -> bool:
        return bool(connection.execute(text("""
            SELECT EXISTS(SELECT 1 FROM career_assistant.external_chat_operations
                WHERE account_id = :account AND submission_started AND NOT execution_settled AND expires_at > :now)
        """), {"account": account_id, "now": now}).scalar_one())

    @staticmethod
    def _calibrate_retention(connection, account_id: UUID | None = None) -> None:
        # 源消息可以晚于操作回执同步，查询时也校准，不能仅在 accepted 那一刻处理。
        connection.execute(text("""
            UPDATE career_assistant.external_chat_operations o
            SET retain_until = LEAST(o.retain_until, m.sent_at + INTERVAL '30 days')
            FROM career_assistant.external_chat_messages m, career_assistant.external_chat_accounts a
            WHERE m.conversation_id = o.conversation_id AND m.platform_message_id = o.platform_message_id
                AND a.id = o.account_id AND m.sender_id = a.platform_account_id
                AND m.direction = 'outgoing' AND m.content_text = o.content_text
                AND m.sent_at + INTERVAL '30 days' < o.retain_until
                AND (CAST(:account AS UUID) IS NULL OR o.account_id = CAST(:account AS UUID))
        """), {"account": account_id})

    def acquire(self, organization_id, actor_id, account_id, *, executor_id: str, takeover=False, now=None) -> dict:
        executor_id = _string(executor_id, "执行端标识", limit=200)
        now = _now(now)
        with self._database.transaction() as connection:
            account = self._account(connection, organization_id, actor_id, account_id)
            active = account["execution_expires_at"] is not None and account["execution_expires_at"] > now
            same = account["executor_id"] == executor_id and active
            if active and not same and not takeover:
                raise ExternalChatExecutionError("execution_busy", "另一个页面正在同步或发送，可查看历史或手动在此接管")
            if not same and self._has_unsettled_submission(connection, account_id, now):
                raise ExternalChatExecutionError("execution_busy", "旧执行端仍在提交消息，请等待本次操作收尾或执行期限结束后再接管")
            self._expire(connection, account_id, now)
            if not same:
                self._invalidate_unsubmitted(connection, account_id, "execution_changed")
            account = connection.execute(text("""
                UPDATE career_assistant.external_chat_accounts SET executor_id = :executor,
                    execution_revision = execution_revision + :increment, execution_expires_at = :expiry
                WHERE id = :account RETURNING *
            """), {"executor": executor_id, "increment": 0 if same else 1,
                   "expiry": now + timedelta(seconds=LEASE_SECONDS), "account": account_id}).mappings().one()
            return {"execution": self._execution(account)}

    def check(self, organization_id, actor_id, account_id, *, executor_id, revision, now=None) -> dict:
        now = _now(now)
        with self._database.transaction() as connection:
            account = self._account(connection, organization_id, actor_id, account_id)
            self._require_owner(account, executor_id, revision, now)
            account = connection.execute(text("""
                UPDATE career_assistant.external_chat_accounts SET execution_expires_at = :expiry
                WHERE id = :account RETURNING *
            """), {"expiry": now + timedelta(seconds=LEASE_SECONDS), "account": account_id}).mappings().one()
            self._expire(connection, account_id, now)
            return {"execution": self._execution(account)}

    def release(self, organization_id, actor_id, account_id, *, executor_id, revision, now=None) -> dict:
        now = _now(now)
        with self._database.transaction() as connection:
            account = self._account(connection, organization_id, actor_id, account_id)
            self._require_owner(account, executor_id, revision, now, allow_paused=True)
            self._invalidate_unsubmitted(connection, account_id, "execution_released")
            account = connection.execute(text("""
                UPDATE career_assistant.external_chat_accounts SET executor_id = NULL,
                    execution_revision = execution_revision + 1, execution_expires_at = :now
                WHERE id = :account RETURNING *
            """), {"account": account_id, "now": now}).mappings().one()
            return {"execution": self._execution(account)}

    def pause(self, organization_id, actor_id, account_id, *, code, message, now=None) -> dict:
        code = _string(code, "暂停原因代码", limit=100)
        message = _string(message, "暂停原因", limit=1000)
        with self._database.transaction() as connection:
            self._account(connection, organization_id, actor_id, account_id)
            self._invalidate_unsubmitted(connection, account_id, "account_paused")
            account = connection.execute(text("""
                UPDATE career_assistant.external_chat_accounts SET paused = TRUE,
                    pause_code = :code, pause_reason = :reason WHERE id = :account RETURNING *
            """), {"account": account_id, "code": code, "reason": message}).mappings().one()
            return {"execution": self._execution(account)}

    def resume(self, organization_id, actor_id, account_id, *, executor_id, revision, now=None) -> dict:
        now = _now(now)
        with self._database.transaction() as connection:
            account = self._account(connection, organization_id, actor_id, account_id)
            self._require_owner(account, executor_id, revision, now, allow_paused=True)
            account = connection.execute(text("""
                UPDATE career_assistant.external_chat_accounts SET paused = FALSE,
                    pause_code = '', pause_reason = '', execution_expires_at = :expiry
                WHERE id = :account RETURNING *
            """), {"account": account_id, "expiry": now + timedelta(seconds=LEASE_SECONDS)}).mappings().one()
            return {"execution": self._execution(account)}

    def _locked_operation(self, connection, organization_id, actor_id, operation_id):
        account_id = connection.execute(text("""
            SELECT o.account_id FROM career_assistant.external_chat_operations o
            JOIN career_assistant.external_chat_accounts a ON a.id = o.account_id
            WHERE o.client_operation_id = :id AND a.organization_id = :org AND a.actor_id = :actor
        """), {"id": operation_id, "org": organization_id, "actor": actor_id}).scalar_one_or_none()
        if account_id is None:
            raise LookupError("发送操作不存在或无访问权限")
        account = self._account(connection, organization_id, actor_id, account_id)
        self._calibrate_retention(connection, account_id)
        operation = connection.execute(text("SELECT * FROM career_assistant.external_chat_operations WHERE client_operation_id = :id FOR UPDATE"), {"id": operation_id}).mappings().one()
        return account, operation

    @staticmethod
    def _read_operation(connection, operation_id):
        return _public(connection.execute(text(f"SELECT {OPERATION_FIELDS} FROM career_assistant.external_chat_operations WHERE client_operation_id = :id"), {"id": operation_id}).mappings().one())

    def create_operation(self, organization_id, actor_id, conversation_id, *, client_operation_id, executor_id, revision, body, now=None) -> dict:
        body = _string(body, "消息正文")
        operation_id = UUID(str(client_operation_id))
        now = _now(now)
        with self._database.transaction() as connection:
            account_id = connection.execute(text("""
                SELECT c.account_id FROM career_assistant.external_chat_conversations c
                JOIN career_assistant.external_chat_accounts a ON a.id = c.account_id
                WHERE c.id = :id AND a.organization_id = :org AND a.actor_id = :actor
            """), {"id": conversation_id, "org": organization_id, "actor": actor_id}).scalar_one_or_none()
            if account_id is None:
                raise LookupError("外部聊天会话不存在或无访问权限")
            account = self._account(connection, organization_id, actor_id, account_id)
            self._calibrate_retention(connection, account_id)
            existing = connection.execute(text("SELECT * FROM career_assistant.external_chat_operations WHERE client_operation_id = :id"), {"id": operation_id}).mappings().one_or_none()
            if existing is not None:
                if existing["account_id"] != account_id or existing["conversation_id"] != conversation_id or existing["content_text"] != body:
                    raise ExternalChatExecutionError("operation_conflict", "操作标识已使用，修改目标或正文后请重新确认")
                if existing["retain_until"] < now:
                    raise LookupError("发送操作已过保留期限")
                self._expire(connection, account_id, now)
                return {"operation": self._read_operation(connection, operation_id)}
            self._require_owner(account, executor_id, revision, now)
            if self._has_unsettled_submission(connection, account_id, now):
                raise ExternalChatExecutionError("execution_busy", "上一条消息仍在提交，请等待其结果")
            connection.execute(text("""
                INSERT INTO career_assistant.external_chat_operations
                    (client_operation_id, account_id, conversation_id, executor_id, revision, content_text,
                     status, created_at, expires_at, retain_until)
                VALUES (:id, :account, :conversation, :executor, :revision, :body,
                        'pending', :now, :expiry, :retention)
            """), {"id": operation_id, "account": account_id, "conversation": conversation_id,
                   "executor": executor_id, "revision": revision, "body": body, "now": now,
                   "expiry": now + timedelta(seconds=OPERATION_SECONDS), "retention": now + timedelta(days=RETENTION_DAYS)})
            return {"operation": self._read_operation(connection, operation_id)}

    def get_operation(self, organization_id, actor_id, operation_id, *, now=None) -> dict:
        now = _now(now)
        with self._database.transaction() as connection:
            account, operation = self._locked_operation(connection, organization_id, actor_id, operation_id)
            if operation["retain_until"] < now:
                raise LookupError("发送操作已过保留期限")
            self._expire(connection, account["id"], now)
            return {"operation": self._read_operation(connection, operation_id)}

    def list_operations(self, organization_id, actor_id, account_id, *, now=None) -> dict:
        now = _now(now)
        with self._database.transaction() as connection:
            self._account(connection, organization_id, actor_id, account_id)
            self._calibrate_retention(connection, account_id)
            self._expire(connection, account_id, now)
            rows = connection.execute(text(f"""
                SELECT {OPERATION_FIELDS} FROM career_assistant.external_chat_operations
                WHERE account_id = :account AND retain_until >= :now ORDER BY created_at, client_operation_id
            """), {"account": account_id, "now": now}).mappings().all()
            return {"operations": [_public(row) for row in rows]}

    def cancel_operation(self, organization_id, actor_id, operation_id, *, reason="user_cancelled", now=None) -> dict:
        now = _now(now)
        reason = str(reason or "user_cancelled")[:1000]
        with self._database.transaction() as connection:
            account, operation = self._locked_operation(connection, organization_id, actor_id, operation_id)
            if operation["retain_until"] < now:
                raise LookupError("发送操作已过保留期限")
            self._expire(connection, account["id"], now)
            connection.execute(text("""
                UPDATE career_assistant.external_chat_operations SET cancel_requested = TRUE, error = :reason,
                    status = CASE WHEN status = 'pending' THEN 'cancelled'
                                  WHEN status IN ('executing','submitting','submitted') THEN 'unknown' ELSE status END
                WHERE client_operation_id = :id AND status NOT IN ('accepted','failed','cancelled','expired')
            """), {"id": operation_id, "reason": reason})
            return {"operation": self._read_operation(connection, operation_id)}

    def transition(self, organization_id, actor_id, operation_id, *, executor_id, revision, status,
                   platform_message_id=None, error=None, now=None) -> dict:
        if not isinstance(status, str) or status not in STATUSES:
            raise ValueError("未知的发送操作状态")
        now = _now(now)
        with self._database.transaction() as connection:
            account, operation = self._locked_operation(connection, organization_id, actor_id, operation_id)
            if operation["retain_until"] < now:
                raise LookupError("发送操作已过保留期限")
            if operation["executor_id"] != executor_id or operation["revision"] != revision:
                raise ExternalChatExecutionError("execution_lost", "该发送操作不属于当前执行资格")
            self._expire(connection, account["id"], now)
            operation = connection.execute(text("SELECT * FROM career_assistant.external_chat_operations WHERE client_operation_id = :id"), {"id": operation_id}).mappings().one()
            current = operation["status"]
            if status == current and not (status == "unknown" and not operation["execution_settled"]):
                return {"operation": self._read_operation(connection, operation_id), "transitioned": False}
            if status in {"executing", "submitting"}:
                self._require_owner(account, executor_id, revision, now)
                expected = "pending" if status == "executing" else "executing"
                if current != expected or operation["cancel_requested"] or operation["expires_at"] <= now:
                    return {"operation": self._read_operation(connection, operation_id), "transitioned": False}
                if status == "submitting" and self._has_unsettled_submission(connection, account["id"], now):
                    raise ExternalChatExecutionError("execution_busy", "其他消息仍在提交，请等待其结果")
            elif status == "submitted":
                # 取消可能先到服务端；原执行端仍须能够报告已经发生的点击。
                if not operation["submission_started"] or current not in {"submitting", "unknown"}:
                    return {"operation": self._read_operation(connection, operation_id), "transitioned": False}
            elif status == "accepted":
                if current not in {"submitting", "submitted", "unknown"} or not operation["submission_started"]:
                    raise ExternalChatExecutionError("operation_transition_invalid", "尚无可核对的提交动作")
                platform_message_id = _string(platform_message_id, "平台消息标识", limit=500)
                # 先按显式平台 ID 定位，再核对来源；绝不按正文/时间搜索猜测关联。
                evidence = connection.execute(text("""
                    SELECT sender_id, direction, content_text, sent_at FROM career_assistant.external_chat_messages
                    WHERE conversation_id = :conversation AND platform_message_id = :message_id
                """), {"conversation": operation["conversation_id"], "message_id": platform_message_id}).mappings().one_or_none()
                if (evidence is None or evidence["direction"] != "outgoing"
                        or evidence["sender_id"] != account["platform_account_id"]
                        or evidence["content_text"] != operation["content_text"]
                        # 平台源时间可能只有秒精度，仅允许本次创建所在秒内的舍入误差。
                        or evidence["sent_at"] < operation["created_at"].replace(microsecond=0)):
                    raise ExternalChatExecutionError("evidence_missing", "尚无与本次操作明确对应的源消息证据，结果仍待核对")
            elif status == "failed":
                if current in {"accepted", "cancelled", "expired", "failed"}:
                    return {"operation": self._read_operation(connection, operation_id), "transitioned": False}
                if operation["submission_started"]:
                    # 确未点击的执行端可以报告未提交；其余只接收明确的业务拒绝格式。
                    no_click = error == "not_submitted" and not operation["execution_settled"]
                    if not no_click and not _business_rejection(error):
                        status = "unknown"
                        error = "evidence_missing"
            elif status in {"cancelled", "expired"}:
                if current in {"submitted", "accepted"} or operation["execution_settled"] or current in {"cancelled", "expired", "failed"}:
                    return {"operation": self._read_operation(connection, operation_id), "transitioned": False}
                if current == "unknown" and error != "not_submitted":
                    raise ExternalChatExecutionError("operation_transition_invalid", "待核对操作只有确未点击证据才可标记未提交")
            elif status == "unknown":
                if current not in {"executing", "submitting", "submitted", "unknown"}:
                    return {"operation": self._read_operation(connection, operation_id), "transitioned": False}
            else:
                raise ExternalChatExecutionError("operation_transition_invalid", "不允许回退或重新执行旧操作")
            settled = status in {"submitted", "accepted", "failed", "cancelled", "expired", "unknown"}
            connection.execute(text("""
                UPDATE career_assistant.external_chat_operations SET status = :status,
                    submission_started = submission_started OR :started,
                    execution_settled = execution_settled OR :settled,
                    platform_message_id = COALESCE(:message_id, platform_message_id), error = :error
                WHERE client_operation_id = :id
            """), {"id": operation_id, "status": status, "started": status == "submitting", "settled": settled,
                   "message_id": platform_message_id, "error": str(error or "")[:1000]})
            if status == "accepted":
                # 取得同一会话真实源时间时只缩短截止点，不延长最初保留期限。
                self._calibrate_retention(connection, account["id"])
            return {"operation": self._read_operation(connection, operation_id), "transitioned": True}

    def purge_expired(self, *, now=None) -> int:
        now = _now(now)
        with self._database.transaction() as connection:
            self._calibrate_retention(connection)
            return connection.execute(text("DELETE FROM career_assistant.external_chat_operations WHERE retain_until < :now"), {"now": now}).rowcount
