"""外部聊天执行互斥、发送状态竞态与保留规则的本机 PostgreSQL 回归。"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text

from test_external_chat import NOW, conversation_id, local_database, payload, store, sync
from src.career_assistant.persistence import external_chat_execution_repository as execution_module
from src.career_assistant.persistence.external_chat_execution_repository import ExternalChatExecutionError, ExternalChatExecutionRepository
from src.career_assistant.persistence.external_chat_repository import ExternalChatRepository
from src.career_assistant.web import external_chat_execution_router, external_chat_router
from src.career_assistant.web import router as career_router


@pytest.fixture
def execution(store, monkeypatch):
    history, org, actor, other = store
    monkeypatch.setattr(execution_module, "utc_now", lambda: NOW)
    aid = UUID(sync(store, payload())["account"]["id"])
    cid = conversation_id(store, str(aid))
    repository = ExternalChatExecutionRepository(history._database)
    lease = repository.acquire(org, actor, aid, executor_id="page-a")["execution"]
    owner = {"executor_id": "page-a", "revision": lease["revision"]}
    return repository, org, actor, other, aid, cid, owner


def create(context, operation_id=None, *, body="用户确认的回复", now=NOW):
    repository, org, actor, _, _, cid, owner = context
    operation_id = operation_id or uuid4()
    return repository.create_operation(org, actor, cid, client_operation_id=operation_id, body=body, now=now, **owner)["operation"]


def transition(context, operation_id, status, **kwargs):
    repository, org, actor, _, _, _, owner = context
    return repository.transition(org, actor, UUID(str(operation_id)), status=status, **owner, **kwargs)


def insert_source(context, mid, *, sender="geek-1", direction="outgoing", body="用户确认的回复", sent_at=NOW, conversation=None):
    repository, _, _, _, _, cid, _ = context
    with repository._database.transaction() as connection:
        connection.execute(text("""
            INSERT INTO career_assistant.external_chat_messages
                (id, conversation_id, platform_message_id, sender_id, direction, message_type, content_text, sent_at, received_at)
            VALUES (:id, :conversation, :mid, :sender, :direction, 'text', :body, :sent, :now)
        """), {"id": uuid4(), "conversation": conversation or cid, "mid": mid, "sender": sender,
               "direction": direction, "body": body, "sent": sent_at, "now": NOW})


def assert_error(code, call):
    with pytest.raises(ExternalChatExecutionError) as captured:
        call()
    assert captured.value.code == code


def test_executor_lease_scope_renew_takeover_and_release(execution):
    repository, org, actor, other, aid, _, owner = execution
    assert_error("execution_busy", lambda: repository.acquire(org, actor, aid, executor_id="page-b"))
    with pytest.raises(LookupError):
        repository.acquire(org, other, aid, executor_id="page-b", takeover=True)
    renewed = repository.check(org, actor, aid, now=NOW + timedelta(seconds=30), **owner)["execution"]
    assert renewed["expires_at"] == "2026-11-09T12:02:00Z"
    newer = repository.acquire(org, actor, aid, executor_id="page-b", takeover=True)["execution"]
    assert newer["revision"] > owner["revision"]
    assert_error("execution_lost", lambda: repository.check(org, actor, aid, **owner))
    assert_error("execution_lost", lambda: repository.release(org, actor, aid, **owner))
    released = repository.release(org, actor, aid, executor_id="page-b", revision=newer["revision"])["execution"]
    assert released["executor_id"] is None
    assert repository.acquire(org, actor, aid, executor_id="page-c")["execution"]["executor_id"] == "page-c"


def test_two_devices_racing_for_expired_lease_have_one_owner(execution):
    repository, org, actor, _, aid, _, _ = execution
    barrier = Barrier(2)

    def claim(executor):
        barrier.wait(timeout=5)
        try:
            return repository.acquire(org, actor, aid, executor_id=executor, now=NOW + timedelta(seconds=91))["execution"]["executor_id"]
        except ExternalChatExecutionError as error:
            return error.code

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ("page-b", "page-c")))
    assert results.count("execution_busy") == 1
    assert len(set(results) & {"page-b", "page-c"}) == 1


def test_pause_persists_across_reload_takeover_and_only_owner_resumes(execution):
    repository, org, actor, _, aid, _, owner = execution
    operation = create(execution)
    repository.pause(org, actor, aid, code="rate_limited", message="请处理平台限制")
    reloaded = ExternalChatExecutionRepository(repository._database)
    assert_error("account_paused", lambda: reloaded.check(org, actor, aid, **owner))
    lease = reloaded.acquire(org, actor, aid, executor_id="page-b", takeover=True)["execution"]
    assert lease["paused"] and lease["pause_code"] == "rate_limited"
    assert_error("execution_lost", lambda: reloaded.resume(org, actor, aid, **owner))
    resumed = reloaded.resume(org, actor, aid, executor_id="page-b", revision=lease["revision"])["execution"]
    assert not resumed["paused"] and resumed["pause_reason"] == ""
    assert repository.get_operation(org, actor, UUID(operation["client_operation_id"]))["operation"]["status"] == "cancelled"


def test_operation_uuid_is_idempotent_and_changed_text_conflicts(execution):
    repository, org, actor, _, _, _, _ = execution
    operation_id = uuid4()
    first = create(execution, operation_id)
    assert create(execution, operation_id) == first
    assert_error("operation_conflict", lambda: create(execution, operation_id, body="已改变正文"))
    transition(execution, operation_id, "executing")
    assert create(execution, operation_id)["status"] == "executing"
    assert len(repository.list_operations(org, actor, UUID(first["account_id"]))["operations"]) == 1
    # 相同正文经过新确认具有独立操作 ID，不按文字去重。
    assert create(execution)["client_operation_id"] != first["client_operation_id"]


def test_concurrent_duplicate_claim_uses_cas_once(execution):
    operation = create(execution)
    operation_id = UUID(operation["client_operation_id"])
    barrier = Barrier(2)

    def claim(_):
        barrier.wait(timeout=5)
        return transition(execution, operation_id, "executing")["transitioned"]

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sorted(pool.map(claim, range(2))) == [False, True]


def test_30_second_execution_deadline_never_extends_on_renew_or_recreate(execution):
    repository, org, actor, _, aid, _, owner = execution
    operation = create(execution)
    oid = UUID(operation["client_operation_id"])
    repository.check(org, actor, aid, now=NOW + timedelta(seconds=29), **owner)
    assert create(execution, oid, now=NOW + timedelta(seconds=29))["expires_at"] == operation["expires_at"]
    exact = repository.get_operation(org, actor, oid, now=NOW + timedelta(seconds=30))["operation"]
    assert exact["status"] == "expired"
    assert transition(execution, oid, "executing", now=NOW + timedelta(seconds=30))["transitioned"] is False
    assert create(execution, oid, now=NOW + timedelta(seconds=31))["status"] == "expired"


def test_lost_executing_becomes_unknown_and_old_id_cannot_restart(execution):
    repository, org, actor, _, _, _, _ = execution
    operation = create(execution)
    oid = UUID(operation["client_operation_id"])
    transition(execution, oid, "executing")
    expired = repository.get_operation(org, actor, oid, now=NOW + timedelta(seconds=30))["operation"]
    assert expired["status"] == "unknown"
    assert expired["cancel_requested"]
    assert not transition(execution, oid, "submitting")["transitioned"]
    assert create(execution, oid)["status"] == "unknown"


def test_pending_cancel_wins_before_claim(execution):
    repository, org, actor, _, _, _, _ = execution
    operation = create(execution)
    oid = UUID(operation["client_operation_id"])
    cancelled = repository.cancel_operation(org, actor, oid)["operation"]
    assert cancelled["status"] == "cancelled" and cancelled["cancel_requested"]
    assert not transition(execution, oid, "executing")["transitioned"]


def test_cancel_after_claim_requires_no_click_evidence_to_finish(execution):
    repository, org, actor, _, _, _, _ = execution
    operation = create(execution)
    oid = UUID(operation["client_operation_id"])
    transition(execution, oid, "executing")
    assert repository.cancel_operation(org, actor, oid)["operation"]["status"] == "unknown"
    assert not transition(execution, oid, "submitting")["transitioned"]
    assert_error("operation_transition_invalid", lambda: transition(execution, oid, "cancelled"))
    assert transition(execution, oid, "cancelled", error="not_submitted")["operation"]["status"] == "cancelled"


def test_submitting_blocks_takeover_until_settled_or_original_deadline(execution):
    repository, org, actor, _, aid, _, owner = execution
    oid = UUID(create(execution)["client_operation_id"])
    transition(execution, oid, "executing")
    transition(execution, oid, "submitting")
    repository.cancel_operation(org, actor, oid)
    assert_error("execution_busy", lambda: repository.acquire(org, actor, aid, executor_id="page-b", takeover=True))
    # 取消与点击交错时，原执行端仍能诚实报告点击，不伪装成已取消。
    assert transition(execution, oid, "submitted")["operation"]["status"] == "submitted"
    assert repository.acquire(org, actor, aid, executor_id="page-b", takeover=True)["execution"]["revision"] > owner["revision"]
    assert not transition(execution, oid, "cancelled", error="not_submitted")["transitioned"]


def test_submitting_disconnection_unknown_does_not_deadlock_future_takeover(execution):
    repository, org, actor, _, aid, _, _ = execution
    oid = UUID(create(execution)["client_operation_id"])
    transition(execution, oid, "executing")
    transition(execution, oid, "submitting")
    repository.acquire(org, actor, aid, executor_id="page-b", takeover=True, now=NOW + timedelta(seconds=30))
    assert repository.get_operation(org, actor, oid)["operation"]["status"] == "unknown"
    assert_error("execution_lost", lambda: transition(execution, oid, "executing"))


def test_acceptance_requires_source_id_and_cannot_turn_into_cancelled(execution):
    repository, org, actor, _, _, _, _ = execution
    oid = UUID(create(execution)["client_operation_id"])
    transition(execution, oid, "executing")
    transition(execution, oid, "submitting")
    transition(execution, oid, "submitted")
    with pytest.raises(ValueError):
        transition(execution, oid, "accepted")
    assert_error("evidence_missing", lambda: transition(execution, oid, "accepted", platform_message_id="90071992547409931"))
    assert repository.get_operation(org, actor, oid)["operation"]["status"] == "submitted"
    insert_source(execution, "90071992547409931")
    accepted = transition(execution, oid, "accepted", platform_message_id="90071992547409931")["operation"]
    assert accepted["platform_message_id"] == "90071992547409931"
    assert repository.cancel_operation(org, actor, oid)["operation"]["status"] == "accepted"
    assert not transition(execution, oid, "cancelled", error="not_submitted")["transitioned"]


def test_explicit_platform_rejection_after_click_can_finish_as_failed(execution):
    oid = UUID(create(execution)["client_operation_id"])
    transition(execution, oid, "executing")
    transition(execution, oid, "submitting")
    transition(execution, oid, "submitted")
    assert transition(execution, oid, "failed")["operation"]["status"] == "unknown"
    assert transition(execution, oid, "failed", error="platform_rejected:message_blocked:平台明确拒绝该消息")["operation"]["status"] == "failed"
    assert not transition(execution, oid, "executing")["transitioned"]


@pytest.mark.parametrize("reason", ["timeout", "network_error", "unknown", "platform_rejected", "platform_rejected::缺少业务代码", "platform_rejected:timeout:超时"])
def test_transport_or_incomplete_evidence_keeps_clicked_operation_unknown(execution, reason):
    repository, org, actor, _, _, _, _ = execution
    oid = UUID(create(execution)["client_operation_id"])
    transition(execution, oid, "executing")
    transition(execution, oid, "submitting")
    transition(execution, oid, "submitted")
    result = transition(execution, oid, "failed", error=reason)["operation"]
    assert result["status"] == "unknown" and result["error"] == "evidence_missing"
    assert repository.get_operation(org, actor, oid)["operation"]["status"] == "unknown"


@pytest.mark.parametrize("source", [
    {"sender": "peer-1", "direction": "incoming"},
    {"sender": "another-geek"},
    {"body": "相同会话的另一条正文"},
])
def test_explicit_source_id_with_wrong_direction_sender_or_text_is_not_acceptance(execution, source):
    repository, org, actor, _, _, _, _ = execution
    oid = UUID(create(execution)["client_operation_id"])
    transition(execution, oid, "executing")
    transition(execution, oid, "submitting")
    transition(execution, oid, "submitted")
    transition(execution, oid, "unknown")
    insert_source(execution, "wrong-source", **source)
    assert_error("evidence_missing", lambda: transition(execution, oid, "accepted", platform_message_id="wrong-source"))
    assert repository.get_operation(org, actor, oid)["operation"]["status"] == "unknown"


def test_matching_text_from_another_conversation_is_not_acceptance(execution):
    repository, org, actor, _, aid, _, _ = execution
    oid = UUID(create(execution)["client_operation_id"])
    transition(execution, oid, "executing")
    transition(execution, oid, "submitting")
    transition(execution, oid, "submitted")
    other_cid = uuid4()
    with repository._database.transaction() as connection:
        connection.execute(text("""
            INSERT INTO career_assistant.external_chat_conversations (id, account_id, platform_conversation_id, peer_id, last_synced_at)
            VALUES (:id, :account, 'peer-2', 'peer-2', :now)
        """), {"id": other_cid, "account": aid, "now": NOW})
    insert_source(execution, "other-source", conversation=other_cid)
    assert_error("evidence_missing", lambda: transition(execution, oid, "accepted", platform_message_id="other-source"))
    assert repository.get_operation(org, actor, oid)["operation"]["platform_message_id"] is None


def test_operation_retention_query_boundary_and_physical_purge(execution):
    repository, org, actor, _, aid, _, _ = execution
    oid = UUID(create(execution)["client_operation_id"])
    boundary = NOW + timedelta(days=30)
    assert repository.get_operation(org, actor, oid, now=boundary)["operation"]["text"] == "用户确认的回复"
    later = boundary + timedelta(microseconds=1)
    assert repository.list_operations(org, actor, aid, now=later)["operations"] == []
    with pytest.raises(LookupError):
        repository.get_operation(org, actor, oid, now=later)
    assert repository.purge_expired(now=later) >= 1
    with repository._database.transaction() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM career_assistant.external_chat_operations WHERE client_operation_id = :id"), {"id": oid}).scalar_one() == 0


def test_late_source_time_can_only_shorten_original_retention(execution):
    repository, org, actor, _, aid, cid, _ = execution
    created_at = NOW + timedelta(microseconds=750000)
    oid = UUID(create(execution, now=created_at)["client_operation_id"])
    transition(execution, oid, "executing", now=created_at)
    transition(execution, oid, "submitting", now=created_at)
    transition(execution, oid, "submitted", now=created_at)
    transition(execution, oid, "unknown", now=created_at)
    assert_error("evidence_missing", lambda: transition(execution, oid, "accepted", platform_message_id="source-id", now=created_at))
    assert repository.get_operation(org, actor, oid, now=created_at)["operation"]["status"] == "unknown"
    # 回执线索先到不能提升状态，真实源消息导入并逐字段核对后才能接受。
    # 源平台只精确到秒时，同秒源时间可略早于创建时间；不能拿昨天的消息作本次证据。
    source_time = NOW
    insert_source(execution, "source-id", sent_at=source_time)
    assert transition(execution, oid, "accepted", platform_message_id="source-id", now=created_at)["operation"]["status"] == "accepted"
    shortened_boundary = source_time + timedelta(days=30)
    assert repository.get_operation(org, actor, oid, now=shortened_boundary)["operation"]["status"] == "accepted"
    assert repository.list_operations(org, actor, aid, now=shortened_boundary + timedelta(microseconds=1))["operations"] == []
    with pytest.raises(LookupError):
        repository.get_operation(org, actor, oid, now=shortened_boundary + timedelta(microseconds=1))
    with repository._database.transaction() as connection:
        # 即使此后出现晚于确认时间的源时间，也不能把保留截止点向后延。
        connection.execute(text("UPDATE career_assistant.external_chat_messages SET sent_at = :sent WHERE conversation_id = :id"), {"sent": NOW + timedelta(hours=1), "id": cid})
    with pytest.raises(LookupError):
        repository.get_operation(org, actor, oid, now=shortened_boundary + timedelta(seconds=1))


def test_cancel_racing_claim_never_leaves_an_executable_operation(execution):
    repository, org, actor, _, _, _, _ = execution
    oid = UUID(create(execution)["client_operation_id"])
    barrier = Barrier(2)

    def claim():
        barrier.wait(timeout=5)
        return transition(execution, oid, "executing")

    def cancel():
        barrier.wait(timeout=5)
        return repository.cancel_operation(org, actor, oid)

    with ThreadPoolExecutor(max_workers=2) as pool:
        claim_result, cancel_result = pool.submit(claim), pool.submit(cancel)
        claim_result.result(timeout=5)
        cancel_result.result(timeout=5)
    result = repository.get_operation(org, actor, oid)["operation"]
    assert result["cancel_requested"] and result["status"] in {"cancelled", "unknown"}
    assert transition(execution, oid, "submitting")["transitioned"] is False


def test_api_actor_scope_fields_errors_and_recovery(execution, monkeypatch):
    repository, org, actor, other, aid, cid, owner = execution
    current = SimpleNamespace(organization_id=org, actor_id=actor)
    monkeypatch.setattr(career_router, "get_request_actor", lambda: current)
    monkeypatch.setattr(career_router, "get_career_read_services", lambda request: SimpleNamespace(database=repository._database))
    monkeypatch.setattr(career_router, "get_career_services", lambda request: pytest.fail("执行 API 不初始化 LLM"))
    app = FastAPI()
    app.include_router(external_chat_router.router)
    app.include_router(external_chat_execution_router.router)
    client = TestClient(app)
    prefix = "/api/career/external-chat"
    assert client.post(f"{prefix}/accounts/{aid}/execution", json={"executor_id": "page-b"}).json()["detail"]["code"] == "execution_busy"
    oid = str(uuid4())
    created = client.post(f"{prefix}/conversations/{cid}/operations", json={**owner, "client_operation_id": oid, "text": "最终确认正文"})
    assert created.status_code == 200
    assert {"client_operation_id", "account_id", "conversation_id", "text", "status", "created_at", "expires_at", "executor_id", "revision", "cancel_requested"} <= set(created.json()["operation"])
    assert client.get(f"{prefix}/operations/{oid}").json() == created.json()
    claimed = client.post(f"{prefix}/operations/{oid}/transition", json={**owner, "status": "executing"})
    assert claimed.status_code == 200 and claimed.json()["transitioned"]
    cancelled = client.post(f"{prefix}/operations/{oid}/cancel", json={"reason": "page_hidden"})
    assert cancelled.json()["operation"]["status"] == "unknown"
    assert client.post(f"{prefix}/accounts/{aid}/execution/check", json={"executor_id": "page-a", "revision": True}).status_code == 422
    current.actor_id = other
    assert client.get(f"{prefix}/operations/{oid}").status_code == 404
    assert client.get(f"{prefix}/accounts/{aid}/operations").status_code == 404
    assert client.post(f"{prefix}/operations/{oid}/cancel", json={}).status_code == 404
    assert client.post(f"{prefix}/accounts/{aid}/pause", json={"code": "rate_limited", "message": "他人无权暂停"}).status_code == 404


def test_installed_api_cleanup_physically_removes_expired_operation_text(execution, monkeypatch):
    repository, org, actor, _, _, _, _ = execution
    oid = UUID(create(execution)["client_operation_id"])
    monkeypatch.setattr(execution_module, "utc_now", lambda: NOW + timedelta(days=30, microseconds=1))
    monkeypatch.setattr(external_chat_router, "_cleanup_repository", lambda app: ExternalChatRepository(repository._database))
    app = FastAPI()
    external_chat_router.install_external_chat_api(app)
    assert "/api/career/external-chat/operations/{client_operation_id}" in app.openapi()["paths"]
    with TestClient(app):
        with repository._database.transaction() as connection:
            assert connection.execute(text("SELECT COUNT(*) FROM career_assistant.external_chat_operations WHERE client_operation_id = :id"), {"id": oid}).scalar_one() == 0
    assert app.state.external_chat_cleanup_task is None
