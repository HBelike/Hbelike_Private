"""外部聊天规范化、真实 PostgreSQL 和 API 回归；只连接本机测试库。"""

from __future__ import annotations

import importlib.util
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from dotenv import dotenv_values
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError

from src.career_assistant import external_chat
from src.career_assistant.external_chat import normalize_snapshot
from src.career_assistant.persistence import external_chat_repository
from src.career_assistant.persistence.database import CareerDatabase
from src.career_assistant.persistence.external_chat_repository import ExternalChatRepository
from src.career_assistant.web import external_chat_router
from src.career_assistant.web import router as career_router
from src.platform_access.contracts import PlatformRole


NOW = datetime(2026, 11, 9, 12, 0, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[1]


def payload(*, messages=(), summary_at=None, summary="最新回复", unread=2):
    return {
        "account": {"platform": "boss", "platform_account_id": "geek-1", "display_name": "我的 BOSS"},
        "conversations": [{
            "platform_conversation_id": "peer-1", "peer_id": "peer-1", "peer_name": "招聘者",
            "company": "测试公司", "job_title": "开发工程师", "unread_count": unread,
            "last_message_at": (summary_at or NOW).isoformat(), "last_message_preview": summary,
            "source_url": "https://www.zhipin.com/web/geek/chat?id=peer-1&securityId=transient",
        }],
        "messages": list(messages),
    }


def message(mid="mid-1", *, sent_at=NOW, body="HR 的真实回复", kind="text"):
    return {"platform_conversation_id": "peer-1", "platform_message_id": mid, "sender_id": "peer-1",
            "direction": "incoming", "message_type": kind, "text": body,
            "sent_at": sent_at.isoformat() if isinstance(sent_at, datetime) else sent_at,
            "source_url": "https://www.zhipin.com/web/geek/chat?id=peer-1"}


def test_normalization_keeps_cutoff_and_uses_source_time():
    cutoff = NOW - timedelta(days=30)
    snapshot = normalize_snapshot(payload(messages=[message("edge", sent_at=cutoff), message("old", sent_at=cutoff - timedelta(microseconds=1))], summary_at=cutoff - timedelta(seconds=1)), now=NOW)
    assert [item["platform_message_id"] for item in snapshot.messages] == ["edge"]
    assert snapshot.conversations[0]["last_message_preview"] == ""
    assert snapshot.conversations[0]["unread_count"] is None
    assert "securityId" not in snapshot.conversations[0]["source_url"]


@pytest.mark.parametrize("when", [None, "", "not-a-time", "2026-11-09T12:00:00"])
def test_unknown_or_naive_time_is_not_replaced_with_import_time(when):
    with pytest.raises(ValueError, match="时间"):
        normalize_snapshot(payload(messages=[message(sent_at=when)]), now=NOW)


def test_media_content_is_only_a_real_type_placeholder_and_ids_are_strings():
    snapshot = normalize_snapshot(payload(messages=[message("90071992547409931", kind="image", body="data:image/png;base64,secret")]), now=NOW)
    assert snapshot.messages[0]["text"] == "[图片]"
    assert snapshot.messages[0]["platform_message_id"] == "90071992547409931"
    bad = payload()
    bad["conversations"][0]["security_id"] = "secret"
    with pytest.raises(ValueError):
        normalize_snapshot(bad, now=NOW)
    bad = payload()
    bad["account"]["actor_id"] = str(uuid4())
    with pytest.raises(ValueError):
        normalize_snapshot(bad, now=NOW)


def test_original_timezone_and_text_are_preserved_without_future_import():
    item = message(body="  HR 原文\n含换行  ")
    item["sent_at"] = "2026-11-09T20:00:00+08:00"
    snapshot = normalize_snapshot(payload(messages=[item, message("future", sent_at=NOW + timedelta(seconds=1))]), now=NOW)
    assert len(snapshot.messages) == 1
    assert snapshot.messages[0]["sent_at"] == NOW
    assert snapshot.messages[0]["text"] == "  HR 原文\n含换行  "


@pytest.fixture(scope="module")
def local_database():
    value = os.getenv("EXTERNAL_CHAT_TEST_DATABASE_URL") or os.getenv("CAREER_DATABASE_URL") or dotenv_values(ROOT / ".env.career-assistant").get("CAREER_DATABASE_URL")
    if not value:
        pytest.skip("真实 SQL 回归需要本地 PostgreSQL 配置")
    url = make_url(value)
    if url.host not in {"127.0.0.1", "localhost", "::1"}:
        pytest.skip("只允许连接 loopback PostgreSQL 测试库")
    url = url.set(drivername="postgresql+psycopg")
    database_name = "codex_external_chat_test_" + uuid4().hex[:12]
    assert re.fullmatch(r"codex_external_chat_test_[a-f0-9]{12}", database_name)
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 3})
    created = False
    database = None
    try:
        try:
            with admin.connect() as connection:
                connection.execute(text(f'CREATE DATABASE "{database_name}"'))
            created = True
        except SQLAlchemyError:
            pytest.skip("本地 PostgreSQL 未就绪或没有建立隔离测试库的权限")
        database = CareerDatabase(url.set(database=database_name).render_as_string(hide_password=False))
        with database.transaction() as connection:
            connection.execute(text("CREATE SCHEMA career_assistant"))
            connection.execute(text("CREATE TABLE career_assistant.organizations (id UUID PRIMARY KEY)"))
            connection.execute(text("CREATE TABLE career_assistant.actors (id UUID PRIMARY KEY, organization_id UUID REFERENCES career_assistant.organizations(id))"))
            spec = importlib.util.spec_from_file_location("external_chat_migration", ROOT / "migrations/versions/20261109_35_external_chat.py")
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)
            migration.op = SimpleNamespace(execute=lambda statement: connection.execute(text(statement)))
            migration.upgrade()
            spec = importlib.util.spec_from_file_location("external_chat_execution_migration", ROOT / "migrations/versions/20261109_37_external_chat_execution.py")
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)
            migration.op = SimpleNamespace(execute=lambda statement: connection.execute(text(statement)))
            migration.upgrade()
        yield database
    finally:
        if database is not None:
            database.close()
        if created:
            # 名称由本测试生成且只连接 loopback，绝不删除配置中原有的数据库。
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{database_name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def store(local_database, monkeypatch):
    organization_id, actor_id, other_id = uuid4(), uuid4(), uuid4()
    with local_database.transaction() as connection:
        connection.execute(text("INSERT INTO career_assistant.organizations VALUES (:id)"), {"id": organization_id})
        for uid in (actor_id, other_id):
            connection.execute(text("INSERT INTO career_assistant.actors VALUES (:id, :org)"), {"id": uid, "org": organization_id})
    monkeypatch.setattr(external_chat, "utc_now", lambda: NOW)
    monkeypatch.setattr(external_chat_repository, "utc_now", lambda: NOW)
    repository = ExternalChatRepository(local_database)
    try:
        yield repository, organization_id, actor_id, other_id
    finally:
        with local_database.transaction() as connection:
            connection.execute(text("DELETE FROM career_assistant.actors WHERE organization_id = :org"), {"org": organization_id})
            connection.execute(text("DELETE FROM career_assistant.organizations WHERE id = :org"), {"org": organization_id})


def sync(store, data):
    repository, org, actor, _ = store
    return repository.sync(org, actor, normalize_snapshot(data, now=NOW))


def conversation_id(store, account_id):
    repository, org, actor, _ = store
    return UUID(repository.list_conversations(org, actor, UUID(account_id), now=NOW)[0]["id"])


def test_postgres_real_upsert_owner_scope_and_media(store):
    repository, org, actor, other = store
    data = payload(messages=[message(), message("media", kind="audio", body="不应保存的音频地址")])
    result = sync(store, data)
    again = sync(store, data)
    assert result["account"]["id"] == again["account"]["id"]
    cid = conversation_id(store, result["account"]["id"])
    page = repository.list_messages(org, actor, cid, now=NOW)
    assert len(page["messages"]) == 2
    assert {item["text"] for item in page["messages"]} == {"HR 的真实回复", "[语音]"}
    assert repository.list_accounts(org, other) == []
    with pytest.raises(LookupError):
        repository.list_conversations(org, other, UUID(result["account"]["id"]))
    with pytest.raises(LookupError):
        repository.list_messages(org, other, cid)
    with pytest.raises(LookupError):
        repository.list_messages(uuid4(), actor, cid)
    # 同一 BOSS UID 不能让两个平台用户的历史合并。
    foreign = repository.sync(org, other, normalize_snapshot(payload(messages=[message(body="另一用户的历史")]), now=NOW))
    assert foreign["account"]["id"] != result["account"]["id"]
    assert repository.list_messages(org, actor, cid)["messages"] == page["messages"]


def test_postgres_retention_import_query_cleanup_and_stale_reimport(store):
    repository, org, actor, _ = store
    boundary = NOW - timedelta(days=30)
    result = sync(store, payload(messages=[message("edge", sent_at=boundary), message("old", sent_at=boundary - timedelta(seconds=1))], summary_at=boundary, summary="边界正文"))
    cid = conversation_id(store, result["account"]["id"])
    assert len(repository.list_messages(org, actor, cid, now=NOW)["messages"]) == 1
    later = NOW + timedelta(microseconds=1)
    # 清理未运行，查询也不能展示到期正文/摘要。
    assert repository.list_messages(org, actor, cid, now=later)["messages"] == []
    conversations = repository.list_conversations(org, actor, UUID(result["account"]["id"]), now=later)
    assert conversations[0]["last_message_preview"] == ""
    assert conversations[0]["last_message_at"] is None
    assert repository.purge_expired(now=later) >= 1
    with repository._database.transaction() as connection:
        assert connection.execute(text("SELECT COUNT(*) FROM career_assistant.external_chat_messages WHERE conversation_id = :id"), {"id": cid}).scalar_one() == 0
        assert connection.execute(text("SELECT last_message_preview FROM career_assistant.external_chat_conversations WHERE id = :id"), {"id": cid}).scalar_one() == ""
    late = normalize_snapshot(payload(messages=[message("edge", sent_at=boundary)], summary_at=boundary), now=later)
    assert repository.sync(org, actor, late)["imported_messages"] == 0
    assert repository.list_messages(org, actor, cid, now=later)["messages"] == []


def test_postgres_same_timestamp_pagination_does_not_skip_or_duplicate(store):
    repository, org, actor, _ = store
    result = sync(store, payload(messages=[message(f"mid-{index}") for index in range(7)]))
    cid = conversation_id(store, result["account"]["id"])
    before = None
    mids = []
    while True:
        page = repository.list_messages(org, actor, cid, limit=2, before=before, now=NOW)
        mids.extend(item["platform_message_id"] for item in page["messages"])
        if not page["has_more"]:
            break
        before = UUID(page["messages"][0]["id"])
    assert len(mids) == len(set(mids)) == 7


def test_postgres_equal_timestamp_preserves_authoritative_list_preview_during_purge(store):
    repository, org, actor, _ = store
    result = sync(store, payload(messages=[message("older-A", body="同毫秒较早的 A"), message("newer-B", body="同毫秒较新的 B")], summary="同毫秒较新的 B"))
    aid = UUID(result["account"]["id"])
    cid = conversation_id(store, result["account"]["id"])
    assert repository.list_conversations(org, actor, aid)[0]["last_message_preview"] == "同毫秒较新的 B"
    with repository._database.transaction() as connection:
        # 内部随机 UUID 只用于稳定分页，绝不能推断平台同毫秒消息的业务先后。
        connection.execute(text("""
            UPDATE career_assistant.external_chat_messages SET id =
                CASE WHEN platform_message_id = 'older-A'
                     THEN CAST('ffffffff-ffff-ffff-ffff-fffffffffff0' AS UUID)
                     ELSE CAST('00000000-0000-0000-0000-000000000010' AS UUID) END
            WHERE conversation_id = :id
        """), {"id": cid})
    repository.purge_expired(now=NOW)
    current = repository.list_conversations(org, actor, aid)[0]
    assert current["last_message_preview"] == "同毫秒较新的 B"
    assert current["last_message_at"] == NOW.isoformat().replace("+00:00", "Z")
    sync(store, payload(summary="同毫秒较新的 B"))
    assert repository.list_conversations(org, actor, aid)[0]["last_message_preview"] == "同毫秒较新的 B"


def test_postgres_identity_conflict_rolls_back_the_whole_sync(store):
    repository, org, actor, _ = store
    result = sync(store, payload(messages=[message()]))
    aid = UUID(result["account"]["id"])
    cid = conversation_id(store, result["account"]["id"])
    changed = payload(messages=[message("new-mid")])
    changed["account"]["display_name"] = "不应部分保存的名称"
    changed["conversations"][0]["peer_id"] = "different-peer"
    with pytest.raises(ValueError, match="招聘者"):
        sync(store, changed)
    assert repository.list_accounts(org, actor)[0]["display_name"] == "我的 BOSS"
    assert repository.list_conversations(org, actor, aid)[0]["peer_id"] == "peer-1"
    assert len(repository.list_messages(org, actor, cid)["messages"]) == 1


def test_postgres_list_summary_survives_until_body_read_then_expires(store):
    repository, org, actor, _ = store
    result = sync(store, payload(summary="仅列表取得的真实摘要"))
    aid = UUID(result["account"]["id"])
    repository.purge_expired(now=NOW)
    assert repository.list_conversations(org, actor, aid, now=NOW)[0]["last_message_preview"] == "仅列表取得的真实摘要"
    repository.purge_expired(now=NOW + timedelta(days=30, seconds=1))
    assert repository.list_conversations(org, actor, aid, now=NOW + timedelta(days=30, seconds=1))[0]["last_message_preview"] == ""


def test_real_api_contract_uses_actor_even_for_admin_and_light_database_only(store, monkeypatch):
    repository, org, actor, other = store
    current = SimpleNamespace(organization_id=org, actor_id=actor, role=PlatformRole.ADMIN)
    monkeypatch.setattr(career_router, "get_request_actor", lambda: current)
    monkeypatch.setattr(career_router, "get_career_read_services", lambda request: SimpleNamespace(database=repository._database))
    monkeypatch.setattr(career_router, "get_career_services", lambda request: pytest.fail("历史 API 不得初始化 LLM 服务"))
    app = FastAPI()
    app.include_router(external_chat_router.router)
    client = TestClient(app)
    response = client.post("/api/career/external-chat/sync", json=payload(messages=[message()]))
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"account", "imported_conversations", "imported_messages"}
    aid = body["account"]["id"]
    assert client.get("/api/career/external-chat/accounts").json()["accounts"][0]["id"] == aid
    conversation = client.get(f"/api/career/external-chat/accounts/{aid}/conversations").json()["conversations"][0]
    assert {"id", "account_id", "last_synced_at", "peer_id"} <= set(conversation)
    assert "security_id" not in conversation
    cid = conversation["id"]
    records = client.get(f"/api/career/external-chat/conversations/{cid}/messages").json()
    assert records["has_more"] is False
    assert records["messages"][0]["text"] == "HR 的真实回复"
    assert client.post("/api/career/external-chat/sync", json=payload(messages=[message(sent_at=None)])).status_code == 422
    current.actor_id = other
    assert client.get("/api/career/external-chat/accounts").json() == {"accounts": []}
    assert client.get(f"/api/career/external-chat/accounts/{aid}/conversations").status_code == 404
    assert client.get(f"/api/career/external-chat/conversations/{cid}/messages").status_code == 404


def test_cleanup_lifecycle_runs_at_start_repeats_and_stops(monkeypatch):
    from threading import Event

    calls = []
    repeated = Event()

    def purge():
        calls.append(1)
        if len(calls) >= 2:
            repeated.set()

    monkeypatch.setattr(external_chat_router, "_cleanup_repository", lambda app: SimpleNamespace(purge_expired=purge))
    app = FastAPI()
    external_chat_router.install_external_chat_api(app, cleanup_interval_seconds=0.01)
    with TestClient(app):
        assert repeated.wait(timeout=1)
        assert len(calls) >= 2
    assert app.state.external_chat_cleanup_task is None
    assert app.state.external_chat_cleanup_stop is None


def test_unconfigured_preview_does_not_start_cleanup(monkeypatch):
    monkeypatch.delenv("CAREER_DATABASE_URL", raising=False)
    monkeypatch.setattr(career_router, "_load_career_environment", lambda root: None)
    monkeypatch.setattr(career_router, "get_career_read_services", lambda request: pytest.fail("未配置数据库的预览不得初始化仓储"))
    app = FastAPI()
    app.state.career_assistant_project_root = ROOT
    external_chat_router.install_external_chat_api(app)
    with TestClient(app):
        assert app.state.external_chat_cleanup_task is None
