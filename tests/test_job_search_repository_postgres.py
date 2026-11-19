"""显式启用的本机 PostgreSQL 求职批次集成测试，只创建和删除合成组织。"""
import os
import json
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
from sqlalchemy import text

from src.career_assistant.persistence.database import CareerDatabase
from src.career_assistant.persistence.job_search_repository import JobSearchRepository


@pytest.fixture
def store():
    if os.environ.get("RUN_JOB_SEARCH_POSTGRES_TESTS") != "1":
        pytest.skip("真实数据库测试需显式 RUN_JOB_SEARCH_POSTGRES_TESTS=1")
    from dotenv import dotenv_values
    values = {**dotenv_values('.env'), **dotenv_values('.env.career-assistant'), **os.environ}
    url = values.get("CAREER_DATABASE_URL", "")
    parsed = urlsplit(url)
    assert parsed.hostname in ("localhost", "127.0.0.1") and parsed.port == 54329
    database = CareerDatabase(url)
    organization, actor, conversation, message = (uuid4() for _ in range(4))
    with database.transaction() as connection:
        connection.execute(text("INSERT INTO career_assistant.organizations(id,slug,display_name) VALUES (:id,:slug,'批次集成测试')"), {"id": organization, "slug": f"job-search-qa-{organization}"})
        connection.execute(text("INSERT INTO career_assistant.actors(id,organization_id,display_name,actor_type) VALUES (:id,:organization,'合成候选人','local')"), {"id": actor, "organization": organization})
        connection.execute(text("INSERT INTO career_assistant.conversations(id,organization_id,actor_id,title) VALUES (:id,:organization,:actor,'批次集成测试')"), {"id": conversation, "organization": organization, "actor": actor})
        connection.execute(text("""INSERT INTO career_assistant.messages(id,conversation_id,role,content_text,ui_payload) VALUES (:id,:conversation,'assistant','合成搜索卡',CAST(:payload AS jsonb))"""), {"id": message, "conversation": conversation, "payload": json.dumps({"type": "job_search", "version": 2, "auto_analyze": True})})
    try:
        yield JobSearchRepository(database), actor, conversation, message
    finally:
        with database.transaction() as connection:
            connection.execute(text("DELETE FROM career_assistant.organizations WHERE id=:id"), {"id": organization})
        database.close()


def create(store, company):
    repo, actor, conversation, message = store
    return repo.create_batch(actor, conversation, {"source_message_id": str(message), "conditions": {"keyword": company, "city": "杭州"}, "request_id": str(uuid4())})["batch"]


def test_history_pagination_decodes_only_page_and_next_marker(store, monkeypatch):
    repo, actor, conversation, _ = store
    batch = create(store, "合成公司")
    saved = repo.save_page(actor, conversation, batch['id'], {'request_id': str(uuid4()), 'page': 1,
        'jobs': [{'id': str(i), 'description': '要求' * 1000} for i in range(12)]})
    decoded = []
    original = repo._jobs
    def track(*args, **kwargs):
        rows = original(*args, **kwargs)
        decoded.append(len(rows))
        return rows
    monkeypatch.setattr(repo, '_jobs', track)
    page = repo.get_jobs(actor, conversation, batch['id'], revision=saved['revision'], limit=1)
    assert len(page['jobs']) == 1 and page['next_cursor'] == 1
    assert decoded == [2]


def test_freezing_counts_and_focus_does_not_decode_job_bodies(store, monkeypatch):
    repo, actor, conversation, _ = store
    batch = create(store, "合成公司")
    saved = repo.save_page(actor, conversation, batch['id'], {'request_id': str(uuid4()), 'page': 1,
        'jobs': [{'id': str(i), 'description': '要求' * 1000} for i in range(12)]})
    def unexpected(*args, **kwargs):
        raise AssertionError('冻结目录不应读取完整岗位正文')
    monkeypatch.setattr(repo, '_jobs', unexpected)
    snapshot = repo.capture_snapshot(actor, conversation, {'active_batch_id': batch['id'],
        'focused_job_ref': saved['job_refs']['11']})
    assert snapshot['catalog'][0]['loaded_count'] == 12
    assert snapshot['focused_job_ref'] == 'S1-J12'


def test_saved_versions_focus_twelfth_and_replayed_request(store):
    repo, actor, conversation, message = store
    batch = create(store, "网易云")
    page = {"request_id": str(uuid4()), "page": 1, "has_more": True,
            "jobs": [{"id": f"job-{i}", "title": f"岗位{i}", "company": "网易云"} for i in range(15)]}
    saved = repo.save_page(actor, conversation, batch["id"], page)
    assert len(saved["job_refs"]) == 15
    assert repo.save_page(actor, conversation, batch["id"], page)["revision"] == saved["revision"]
    focused = saved["job_refs"]["job-11"]
    repo.focus(actor, conversation, batch["id"], {"job_ref": focused})
    with repo._database.transaction() as connection:
        snapshot = repo.capture_snapshot(actor, conversation, {"active_batch_id": batch["id"], "focused_job_ref": focused}, connection=connection)
    repo.save_details(actor, conversation, batch["id"], {"request_id": str(uuid4()), "jobs": [{"job_ref": focused, "description": "长" * 2200 + "必须博士"}]})
    old = repo.resolve_snapshot(actor, conversation, snapshot)
    assert not old["batches"][0]["jobs"][11].get("description")
    current = repo.get_jobs(actor, conversation, batch["id"])
    assert current["jobs"][11]["description"].endswith("必须博士")
    assert current["jobs"][11]["job_ref"] == focused
    repo.save_page(actor, conversation, batch["id"], {**page, "request_id": str(uuid4())})
    assert repo.get_jobs(actor, conversation, batch["id"])["jobs"][11]["description"].endswith("必须博士")
    with pytest.raises(ValueError, match="同一 request_id"):
        repo.save_page(actor, conversation, batch["id"], {**page, "page": 2})


def test_late_pages_are_bound_to_original_batch_and_completed_replays(store):
    repo, actor, conversation, message = store
    first, second = create(store, "网易云"), create(store, "腾讯")
    repo.save_page(actor, conversation, second["id"], {"request_id": str(uuid4()), "page": 1, "jobs": [{"id": "b", "company": "腾讯"}]})
    repo.save_page(actor, conversation, first["id"], {"request_id": str(uuid4()), "page": 2, "jobs": [{"id": "a", "company": "网易云"}]})
    assert [job["company"] for job in repo.get_jobs(actor, conversation, second["id"])["jobs"]] == ["腾讯"]
    complete = {"request_id": str(uuid4())}
    assert repo.complete(actor, conversation, first["id"], complete)["batch"]["search_status"] == "succeeded"
    assert repo.complete(actor, conversation, first["id"], complete)["revision"] == 1
    with pytest.raises(LookupError):
        repo.get_jobs(uuid4(), conversation, first["id"])


def test_legacy_import_has_only_actual_saved_data_and_is_not_duplicated(store):
    repo, actor, conversation, message = store
    with repo._database.transaction() as connection:
        connection.execute(text("""UPDATE career_assistant.messages SET ui_payload=CAST(:payload AS jsonb) WHERE id=:id"""), {"id": message, "payload": json.dumps({"type": "job_search", "version": 1, "conditions": {"keyword": "旧公司", "city": "杭州"}, "result_summary": {"count": 15, "jobs": [{"id": "legacy", "title": "旧岗位"}]}})})
    batches = repo.list_batches(actor, conversation)
    assert len(repo.list_batches(actor, conversation)) == len(batches) == 1
    assert batches[0]["legacy_imported"] and batches[0]["legacy_total_count"] == 15
    assert len(repo.get_jobs(actor, conversation, batches[0]["id"])["jobs"]) == 1


def test_same_content_new_request_does_not_add_revision_and_invalid_focus_is_rejected(store):
    repo, actor, conversation, message = store
    batch = create(store, "网易云")
    page = {"request_id": str(uuid4()), "page": 1, "jobs": [{"id": "same", "title": "同岗位"}]}
    first = repo.save_page(actor, conversation, batch["id"], page)
    assert repo.save_page(actor, conversation, batch["id"], {**page, "request_id": str(uuid4())})["revision"] == first["revision"]
    with pytest.raises(ValueError, match="预览"):
        repo.capture_snapshot(actor, conversation, {"active_batch_id": batch["id"], "focused_job_ref": "S1-J12"})
    with pytest.raises(ValueError, match="版本"):
        repo.get_jobs(actor, conversation, batch["id"], revision=999)


def test_real_api_create_page_focus_and_history(store, monkeypatch):
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.career_assistant.web import router as main_router
    from src.career_assistant.web.job_search_router import router
    repo, actor, conversation, message = store
    monkeypatch.setattr(main_router, "get_request_actor", lambda: SimpleNamespace(actor_id=actor))
    monkeypatch.setattr(main_router, "get_career_read_services", lambda request: SimpleNamespace(database=repo._database))
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        base = f"/api/career/conversations/{conversation}/job-search/batches"
        created = client.post(base, json={"request_id": str(uuid4()), "source_message_id": str(message), "analysis_requested": False, "conditions": {"keyword": "网易云", "city": "杭州"}})
        assert created.status_code == 200
        batch_id = created.json()["batch"]["id"]
        saved = client.post(f"{base}/{batch_id}/pages", json={"request_id": str(uuid4()), "page": 1, "jobs": [{"id": "job", "title": "Agent工程师"}]})
        assert saved.status_code == 200
        reference = saved.json()["job_refs"]["job"]
        assert client.patch(f"{base}/{batch_id}/focus", json={"job_ref": reference}).status_code == 200
        assert client.get(f"{base}/{batch_id}/jobs").json()["jobs"][0]["job_ref"] == reference
        assert len(client.get(base).json()["batches"]) == 1
        assert client.patch(f"{base}/{batch_id}/focus", json={"job_ref": "missing"}).status_code == 422
        finish = {"request_id": str(uuid4())}
        result = client.post(f"{base}/{batch_id}/complete", json=finish)
        assert result.status_code == 200 and result.json()["analysis_turn"] is None
        assert client.post(f"{base}/{batch_id}/complete", json=finish).status_code == 200


def test_concurrent_duplicate_callbacks_commit_one_batch_and_one_version(store):
    from concurrent.futures import ThreadPoolExecutor
    repo, actor, conversation, message = store
    request = {"source_message_id": str(message), "conditions": {"keyword": "网易云", "city": "杭州"}, "request_id": str(uuid4())}
    with ThreadPoolExecutor(max_workers=2) as pool:
        created = list(pool.map(lambda _: repo.create_batch(actor, conversation, request), range(2)))
    assert created[0]["batch"]["id"] == created[1]["batch"]["id"]
    batch_id = created[0]["batch"]["id"]
    page = {"request_id": str(uuid4()), "page": 1, "jobs": [{"id": "same", "company": "网易云"}]}
    with ThreadPoolExecutor(max_workers=2) as pool:
        saved = list(pool.map(lambda _: repo.save_page(actor, conversation, batch_id, page), range(2)))
    assert [item["revision"] for item in saved] == [1, 1]
    assert len(repo.list_batches(actor, conversation)) == 1
    assert len(repo.get_jobs(actor, conversation, batch_id)["jobs"]) == 1
