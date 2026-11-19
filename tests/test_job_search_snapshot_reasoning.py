"""冻结批次的意图、引用和证据选择反例。"""
from types import SimpleNamespace
import json
from unittest.mock import Mock
from uuid import uuid4

from src.career_assistant.job_search_context import resolve_search_scope, snapshot_context_messages
from src.career_assistant.job_search_intent import detect_job_search_intent
from src.career_assistant.job_search_response import detect_route, materialize_route
from src.career_assistant.intake_graph import ModelTurnContext
from src.career_assistant.contracts import ModelCapability
from src.career_assistant.prompt_context import PromptContextService
from src.career_assistant.persistence.model_profile_repository import ModelContextPolicy


def snapshot():
    batches = [{"id": name, "batch_no": index, "revision": 3, "conditions": {"keyword": company, "city": "杭州"},
                "search_status": "succeeded", "loaded_count": 15,
                "jobs": [{"job_ref": f"S{index}-J{n}", "display_index": n, "title": f"{company}岗位{n}",
                          "company": company, "description": "职责" * 1200 + "必须本科，三年经验"} for n in range(1, 16)]}
               for index, (name, company) in enumerate((("a", "网易云"), ("b", "腾讯")), 1)]
    return {"catalog": [{k: v for k, v in batch.items() if k != "jobs"} for batch in batches],
            "batches": batches, "active_batch_id": "b", "focused_job_ref": "S2-J12", "uncommitted_batch_ids": []}


def test_explicit_company_search_is_not_direction_chat():
    for text in ("帮我找腾讯有哪些岗位适合我", "重新搜索上海有哪些岗位适合我"):
        assert detect_job_search_intent(text).action == "open_job_search"
    assert detect_job_search_intent("帮我找腾讯有哪些岗位适合我").keyword == "腾讯"


def test_scope_tracks_focus_and_cross_batch_references():
    assert resolve_search_scope("这个岗位适合我吗", snapshot())["job_refs"] == ["S2-J12"]
    assert resolve_search_scope("比较刚才网易云和腾讯这两批", snapshot())["batch_ids"] == ["a", "b"]
    scope = resolve_search_scope("回到刚才网易云那批", snapshot())
    assert scope["batch_ids"] == ["a"] and scope["mode"] == "view"


def test_compare_all_batches_includes_every_batch_instead_of_only_last_two():
    frozen = snapshot()
    third = {"id": "c", "batch_no": 3, "revision": 1, "conditions": {"keyword": "第三公司", "city": "上海"}, "loaded_count": 1}
    frozen["catalog"].append(third)
    frozen["batches"].append({**third, "jobs": [{"job_ref": "S3-J1", "title": "第三公司岗位", "company": "第三公司"}]})
    for text in ("比较所有批次", "比较全部批次"):
        scope = resolve_search_scope(text, frozen)
        assert scope["batch_ids"] == ["a", "b", "c"]
        payload = json.loads(snapshot_context_messages(frozen, text)[1].content.split(">", 1)[1].rsplit("<", 1)[0])
        assert {job["batch_id"] for job in payload["jobs"]} == {"a", "b", "c"}


def test_nonexistent_explicit_batch_never_falls_back_to_active_batch():
    frozen = snapshot()
    scope = resolve_search_scope("回到第99批", frozen)
    assert scope["ambiguous"] is True
    assert scope["batch_ids"] == []
    payload = json.loads(snapshot_context_messages(frozen, "回到第99批")[1].content.split(">", 1)[1].rsplit("<", 1)[0])
    assert payload["jobs"] == []
    assert [item["batch_no"] for item in payload["available_batches"]] == [1, 2]
    assert all("jobs" not in item for item in payload["available_batches"])


def test_unmatched_explicit_company_requires_clarification_without_alias_guess():
    frozen = snapshot()
    scope = resolve_search_scope("回到刚才网易那批", frozen)
    assert scope["ambiguous"] is True
    assert scope["batch_ids"] == []
    payload = json.loads(snapshot_context_messages(frozen, "回到刚才网易那批")[1].content.split(">", 1)[1].rsplit("<", 1)[0])
    assert payload["jobs"] == []
    assert [item["conditions"]["keyword"] for item in payload["available_batches"]] == ["网易云", "腾讯"]
    assert resolve_search_scope("这些岗位哪些适合我", frozen)["batch_ids"] == ["b"]


def test_normal_chat_has_no_job_payload_and_focus_preserves_tail_requirements():
    assert snapshot_context_messages(snapshot(), "你好", token_budget=3000) == ()
    messages = snapshot_context_messages(snapshot(), "这个岗位适合我吗", token_budget=3000)
    prompt = "\n".join(item.content for item in messages)
    assert "腾讯岗位12" in prompt and "必须本科，三年经验" in prompt
    assert "腾讯岗位1\"" not in prompt


def test_recommendation_contains_all_loaded_candidate_identities():
    messages = snapshot_context_messages(snapshot(), "比较两批岗位并推荐", token_budget=6000)
    prompt = "\n".join(item.content for item in messages)
    assert "S1-J15" in prompt and "S2-J15" in prompt


def test_frozen_directory_route_does_not_read_mutable_message_window():
    active = SimpleNamespace(turn=SimpleNamespace(id=uuid4(), input_kind_codes=()), conversation=SimpleNamespace(actor_id=uuid4(), id=uuid4()))
    context = SimpleNamespace(redacted_user_text="再看看上海的", job_search_snapshot=snapshot())
    repository = SimpleNamespace(list_messages=Mock(side_effect=AssertionError("不能重读可变卡片")))
    route = detect_route(repository, active, context)
    assert route.intent.keyword == "腾讯" and route.intent.city == "上海"


def test_real_16k_prompt_normal_chat_is_not_blocked_by_previous_jds():
    active = SimpleNamespace(turn=SimpleNamespace(id=uuid4()), conversation=SimpleNamespace(id=uuid4(), actor_id=uuid4(), organization_id=uuid4()))
    repository = SimpleNamespace(list_messages=Mock(side_effect=AssertionError("冻结目录不能重读历史")),
        get_valid_summary=Mock(return_value=None), list_completed_dialogue_messages=Mock(return_value=[]))
    context = ModelTurnContext("你好", "", "", frozenset({ModelCapability.TEXT}), False, (), (), 0,
        job_search_snapshot=snapshot())
    resolution = SimpleNamespace(profile=SimpleNamespace(provider_key="fake", model_id="fake",
        context_policy=ModelContextPolicy(context_window_tokens=16384, reserved_output_tokens=4096)))
    prepared = PromptContextService(repository, Mock()).prepare(active, context, resolution)
    assert prepared.context_usage.estimated_input_tokens < 2000
    assert "腾讯岗位" not in str(prepared.messages)


def test_turn_with_unsaved_referenced_data_cannot_substitute_other_company():
    frozen = snapshot()
    frozen["uncommitted_batch_ids"] = ["b"]
    messages = snapshot_context_messages(frozen, "这个岗位适合我吗")
    prompt = str(messages)
    assert '"data_uncommitted": true' in prompt
    assert '"jobs": []' in prompt


def test_snapshot_cancel_targets_the_batch_without_mutable_card_lookup():
    active = SimpleNamespace(turn=SimpleNamespace(id=uuid4(), input_kind_codes=()),
        conversation=SimpleNamespace(actor_id=uuid4(), id=uuid4()))
    context = SimpleNamespace(redacted_user_text="取消搜索", job_search_snapshot=snapshot())
    repository = SimpleNamespace(cancel_job_search_batch=Mock())
    route = detect_route(repository, active, context)
    assert route.cancel_batch_id == "b"
    text, payload, updates = materialize_route(repository, active, route, SimpleNamespace())
    repository.cancel_job_search_batch.assert_called_once_with(active.conversation.actor_id, active.conversation.id, "b")
    assert "停止" in text and payload is None
