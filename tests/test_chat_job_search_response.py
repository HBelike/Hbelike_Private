"""对话求职组件的完整回复分流、历史与卡片数据契约。"""

from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

import pytest

from src.career_assistant.contracts import ModelCapability, ModelSelectionRequest
from src.career_assistant.job_search_cards import (
    card_message_payload,
    new_job_search_card,
    patch_job_search_card,
)
from src.career_assistant.job_search_response import detect_route
from src.career_assistant.intake_graph import ModelTurnContext
from src.career_assistant.prompt_context import PromptContextService
from src.career_assistant.persistence.model_profile_repository import ModelContextPolicy
from src.career_assistant.model_clients import ChatMessage, CompletionUsage
from src.career_assistant.model_gateway import ModelReadiness
from src.career_assistant.persistence.records import MessageRecord, MessageRole
from src.career_assistant.response_runner import CareerResponseRunner
from src.career_assistant.web import router as career_router


class FakeRepository:
    """在内存中实现真正会被回复链使用的消息读写，不访问数据库。"""

    def __init__(self):
        self.messages = []
        self.patches = []

    def list_messages(self, actor_id, conversation_id, *, limit=None):
        result = [item for item in self.messages if item.conversation_id == conversation_id]
        return result[-limit:] if limit else result

    def append_message(self, conversation_id, role, content_text, *, turn_id=None, is_redacted=False, ui_payload=None):
        message = MessageRecord(uuid4(), conversation_id, turn_id, role, content_text,
                                is_redacted, datetime.now(UTC), ui_payload=ui_payload)
        self.messages.append(message)
        return message

    def update_job_search_card(self, actor_id, conversation_id, message_id, patch):
        index = next((index for index, message in enumerate(self.messages)
                      if message.id == message_id and message.conversation_id == conversation_id), None)
        if index is None:
            raise LookupError("卡片不存在")
        previous = self.messages[index]
        updated = replace(previous, ui_payload=patch_job_search_card(previous.ui_payload, patch))
        self.messages[index] = updated
        self.patches.append((message_id, patch))
        return updated


class FakeClient:
    def __init__(self):
        self.prompts = []
        self.intent_error = False
        self.intent_response = "不是合法 JSON"

    def complete(self, profile, env_name, prompt, **kwargs):
        self.prompts.append(prompt)
        if len(prompt) == 1 and "意图路由器" in prompt[0].content:
            if self.intent_error:
                raise TimeoutError("模拟识别超时")
            return self.intent_response
        kwargs["usage_callback"](CompletionUsage(12, 8))
        return "这是正常的文字回答。"

    def stream_complete(self, profile, env_name, prompt, **kwargs):
        self.prompts.append(prompt)
        kwargs["usage_callback"](CompletionUsage(12, 8))
        yield "这是正常的"
        yield "文字回答。"


@pytest.fixture
def setup_runner(monkeypatch):
    repository = FakeRepository()
    actor_id, conversation_id, turn_id = uuid4(), uuid4(), uuid4()
    active = SimpleNamespace(
        conversation=SimpleNamespace(id=conversation_id, actor_id=actor_id, organization_id=uuid4()),
        turn=SimpleNamespace(id=turn_id, input_kind_codes=("text",)),
    )
    loop = SimpleNamespace(repository=repository, mark_turn_succeeded=Mock(return_value=active.turn))
    gateway = SimpleNamespace(resolve=Mock(return_value=SimpleNamespace(
        readiness=ModelReadiness.READY, profile=SimpleNamespace(id=uuid4()),
        credential_env_name="TEST_ONLY", credential="not-a-real-key")))
    client = FakeClient()
    runner = CareerResponseRunner(loop, gateway, chat_client=client,
                                  redactor=SimpleNamespace(enabled=False, redact=lambda text: text))
    monkeypatch.setattr(runner, "_prepare_prompt", lambda active, context, resolution: SimpleNamespace(
        messages=[ChatMessage("system", "你是求职助手"), ChatMessage("user", context.redacted_user_text)],
        context_usage=SimpleNamespace(reserved_output_tokens=512),
    ))
    state = SimpleNamespace(repository=repository, active=active, client=client,
                            gateway=gateway, runner=runner, loop=loop)

    def prepare(text, *, prior_status=None, keyword="Java 开发", city="杭州"):
        if prior_status:
            payload = new_job_search_card(keyword, city, auto_search=False)
            payload["status"] = prior_status
            state.card = repository.append_message(conversation_id, MessageRole.ASSISTANT,
                                                   "找工作表单", turn_id=uuid4(), ui_payload=payload)
        current = repository.append_message(conversation_id, MessageRole.USER, text, turn_id=turn_id)
        context = SimpleNamespace(redacted_user_text=text, required_capabilities=frozenset({ModelCapability.TEXT}),
                                  activated_skills=())
        state.intake = SimpleNamespace(active_turn=active, model_context=context, persisted_message=current)
        state.inbound = SimpleNamespace(model_selection=ModelSelectionRequest())
        return state

    state.prepare = prepare
    return state


@pytest.mark.parametrize("stream", [False, True])
def test_clear_search_creates_persisted_ui_without_gateway_or_model(setup_runner, stream):
    state = setup_runner.prepare("帮我找杭州的 Java 开发岗位")
    state.gateway.resolve.side_effect = AssertionError("明确搜索不应依赖模型")
    if stream:
        events = list(state.runner.stream(state.inbound, state.intake))
        assert [event.event_type for event in events] == ["done"]
        result = events[0].result
    else:
        result = state.runner.run(state.inbound, state.intake)
    assert result.assistant_message.ui_payload["conditions"]["city"] == "杭州"
    assert result.assistant_message.ui_payload["conditions"]["keyword"] == "Java 开发"
    assert result.assistant_message.ui_payload["auto_search"] is True
    assert result.model_was_invoked is False
    assert not state.client.prompts
    assert state.repository.messages[-1] == result.assistant_message
    state.loop.mark_turn_succeeded.assert_called_once()


@pytest.mark.parametrize("stream", [False, True])
def test_unknown_role_invokes_only_semantic_extraction_then_returns_card(setup_runner, stream):
    text = "帮我找杭州咖啡店店长岗位"
    state = setup_runner.prepare(text)
    state.client.intent_response = json.dumps({
        "action": "open_job_search", "keyword": "咖啡店店长", "city": "杭州",
        "evidence": text, "mixed_task": False, "auto_search": True,
    }, ensure_ascii=False)
    if stream:
        result = list(state.runner.stream(state.inbound, state.intake))[-1].result
    else:
        result = state.runner.run(state.inbound, state.intake)
    assert result.assistant_message.ui_payload["conditions"]["keyword"] == "咖啡店店长"
    assert result.assistant_message.ui_payload["auto_search"] is True
    assert len(state.client.prompts) == 1
    assert result.model_was_invoked is True


@pytest.mark.parametrize("stream", [False, True])
def test_explicit_search_without_city_preserves_request_until_client_location(setup_runner, stream):
    state = setup_runner.prepare("帮我找Java岗位")
    if stream:
        result = list(state.runner.stream(state.inbound, state.intake))[-1].result
    else:
        result = state.runner.run(state.inbound, state.intake)
    card = result.assistant_message.ui_payload
    assert card["conditions"]["keyword"] == "Java"
    assert card["conditions"]["city"] is None
    assert card["auto_search"] is True
    assert not state.client.prompts


@pytest.mark.parametrize("stream", [False, True])
def test_real_runner_sends_saved_jobs_and_resume_to_model_on_followup(setup_runner, monkeypatch, stream):
    state = setup_runner.prepare("网易有什么岗位适合我，给我一些推荐", prior_status="searched", keyword="网易云")
    state.repository.update_job_search_card(state.active.conversation.actor_id, state.active.conversation.id,
        state.card.id, {"result_summary": {"count": 15, "jobs": [{"id": "job-1", "title": "Agent工程师",
        "company": "网易云", "description_excerpt": "要求 LangGraph、RAG"}]}})
    state.repository.get_valid_summary = Mock(return_value=None)
    state.repository.list_completed_dialogue_messages = Mock(return_value=[])
    resolution = state.gateway.resolve.return_value
    resolution.profile.provider_key = "fake"
    resolution.profile.model_id = "fake"
    resolution.profile.context_policy = ModelContextPolicy(context_window_tokens=128_000, reserved_output_tokens=4096)
    state.intake.model_context = ModelTurnContext("网易有什么岗位适合我，给我一些推荐", "", "",
        frozenset({ModelCapability.TEXT}), False, (), (), 0, redacted_resume_outline="项目：使用 LangGraph 与 RAG")
    state.runner._prompt_context = PromptContextService(state.repository, Mock())
    monkeypatch.setattr(state.runner, "_prepare_prompt", CareerResponseRunner._prepare_prompt.__get__(state.runner))
    if stream:
        result = list(state.runner.stream(state.inbound, state.intake))[-1].result
    else:
        result = state.runner.run(state.inbound, state.intake)
    assert result.assistant_message.ui_payload is None
    prompt = "\n".join(item.content for item in state.client.prompts[-1])
    assert "网易云" in prompt and "Agent工程师" in prompt
    assert "要求 LangGraph、RAG" in prompt and "项目：使用 LangGraph 与 RAG" in prompt
    assert len(state.repository.patches) == 1


def test_confirmed_profile_prefills_only_controlled_role_header(setup_runner):
    state = setup_runner.prepare("我想找工作")
    state.intake.model_context.context_binding_version = 2
    state.intake.model_context.target_role_context = "公司：测试公司\n岗位：咖啡店店长\n岗位版本：v2\n\n工作地点：北京\n岗位：Python开发\n要求熟悉上海市场"
    result = state.runner.run(state.inbound, state.intake)
    card = result.assistant_message.ui_payload
    assert card["conditions"]["keyword"] == "咖啡店店长"
    assert card["conditions"]["city"] is None
    assert card["auto_search"] is False


def test_unconfirmed_target_text_cannot_prefill_search(setup_runner):
    state = setup_runner.prepare("我想找工作")
    state.intake.model_context.target_role_context = "公司：测试公司\n岗位：Python开发\n\n工作地点：北京"
    result = state.runner.run(state.inbound, state.intake)
    assert result.assistant_message.ui_payload["conditions"] == {"keyword": "", "city": None, "city_source": None}


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("text,mixed", [("你好", False), ("帮我找杭州Java岗位，顺便分析我的简历", True)])
def test_normal_and_mixed_tasks_keep_text_generation(setup_runner, stream, text, mixed):
    state = setup_runner.prepare(text)
    if stream:
        events = list(state.runner.stream(state.inbound, state.intake))
        result = events[-1].result
        assert "".join(event.content for event in events if event.event_type == "delta") == "这是正常的文字回答。"
    else:
        result = state.runner.run(state.inbound, state.intake)
    assert "这是正常的文字回答。" in result.assistant_message.content_text
    assert (result.assistant_message.ui_payload is not None) is mixed
    assert result.model_was_invoked is True
    assert len(state.client.prompts) == 1
    if mixed:
        assert any("同时展示职位搜索组件" in str(item.content) for item in state.client.prompts[0])


def test_editing_conditions_update_original_message_and_done_payload(setup_runner):
    state = setup_runner.prepare("城市改成上海", prior_status="editing")
    result = state.runner.run(state.inbound, state.intake)
    assert result.assistant_message.ui_payload is None
    assert len(result.updated_messages) == 1
    updated = result.updated_messages[0]
    assert updated.id == state.card.id
    assert updated.ui_payload["conditions"] == {"keyword": "Java 开发", "city": "上海", "city_source": "user"}
    assert updated.ui_payload["auto_search"] is False
    assert state.repository.patches[0][0] == state.card.id


def test_searched_card_remains_unchanged_when_new_city_is_requested(setup_runner):
    state = setup_runner.prepare("再看看上海的", prior_status="searched")
    old_payload = state.card.ui_payload.copy()
    result = state.runner.run(state.inbound, state.intake)
    assert not result.updated_messages
    assert result.assistant_message.ui_payload["conditions"]["city"] == "上海"
    assert result.assistant_message.ui_payload["conditions"]["keyword"] == "Java 开发"
    assert result.assistant_message.ui_payload["auto_search"] is True
    assert state.repository.messages[0].ui_payload == old_payload


def test_cancel_persists_dismissed_state_and_returns_updated_message(setup_runner):
    state = setup_runner.prepare("先不找工作了", prior_status="searched")
    result = state.runner.run(state.inbound, state.intake)
    assert result.updated_messages[0].id == state.card.id
    assert result.updated_messages[0].ui_payload["status"] == "dismissed"
    assert state.repository.messages[0].ui_payload["status"] == "dismissed"
    assert result.assistant_message.ui_payload is None


def test_queued_later_messages_do_not_enter_current_route(setup_runner):
    state = setup_runner.prepare("有什么机会")
    state.repository.append_message(state.active.conversation.id, MessageRole.USER,
                                    "帮我找北京Python岗位", turn_id=uuid4())
    state.repository.append_message(state.active.conversation.id, MessageRole.ASSISTANT,
                                    "未来表单", turn_id=uuid4(),
                                    ui_payload=new_job_search_card("Python", "北京", auto_search=True))
    route = detect_route(state.repository, state.active, state.intake.model_context)
    assert route.previous_message is None
    assert route.history == ()


def test_current_message_outside_recent_window_does_not_expose_future_cards(setup_runner):
    state = setup_runner.prepare("有什么机会")
    for _ in range(31):
        state.repository.append_message(state.active.conversation.id, MessageRole.ASSISTANT,
                                        "未来的搜索结果", turn_id=uuid4(),
                                        ui_payload=new_job_search_card("Python", "北京", auto_search=True))
    route = detect_route(state.repository, state.active, state.intake.model_context)
    assert route.previous_message is None
    assert route.history == ()


@pytest.mark.parametrize("stream", [False, True])
def test_semantic_detection_failure_continues_normal_answer(setup_runner, stream):
    state = setup_runner.prepare("有什么机会")
    state.client.intent_error = True
    if stream:
        result = list(state.runner.stream(state.inbound, state.intake))[-1].result
    else:
        result = state.runner.run(state.inbound, state.intake)
    assert result.assistant_message.content_text == "这是正常的文字回答。"
    assert result.assistant_message.ui_payload is None
    assert len(state.client.prompts) == 2


def test_history_and_update_message_serializers_preserve_card(setup_runner):
    state = setup_runner.prepare("帮我找杭州Java岗位")
    result = state.runner.run(state.inbound, state.intake)
    message = result.assistant_message
    assert career_router._message_payload(message)["ui_payload"] == message.ui_payload
    assert card_message_payload(message)["ui_payload"] == message.ui_payload
    assert card_message_payload(message)["id"] == str(message.id)


def test_patch_api_returns_persisted_message(setup_runner, monkeypatch):
    state = setup_runner.prepare("你好", prior_status="editing")
    monkeypatch.setattr(career_router, "get_career_read_services", lambda request: SimpleNamespace(conversation_repository=state.repository))
    monkeypatch.setattr(career_router, "get_request_actor", lambda: SimpleNamespace(actor_id=state.active.conversation.actor_id))
    result = asyncio.run(career_router.update_job_search_card(
        state.active.conversation.id, state.card.id,
        {"status": "searched", "result_summary": {"count": 1, "jobs": [{"title": "Java开发", "company": "测试公司"}]}}, None,
    ))
    assert result["message"]["id"] == str(state.card.id)
    assert result["message"]["ui_payload"]["status"] == "searched"


@pytest.mark.parametrize("patch", [
    {"html": "<script>ignore</script>"},
    {"status": []},
    {"status": "running"},
    {"auto_search": 1},
    {"conditions": {"city": {"name": "杭州"}}},
    {"conditions": {"keyword": ["Java"]}},
    {"conditions": {"latitude": 31.1}},
    {"conditions": {"city_source": "guessed"}},
    {"result_summary": {"count": True, "jobs": []}},
    {"result_summary": {"count": -1, "jobs": []}},
    {"result_summary": {"count": 11, "jobs": [{"title": "开发"}] * 11}},
    {"selected_job": {"description": "不保存完整岗位正文"}},
    {"selected_job": {"title": 123}},
])
def test_patch_rejects_unknown_fields_and_invalid_types_without_mutating_card(patch):
    current = new_job_search_card("Java", "杭州", auto_search=False)
    expected = new_job_search_card("Java", "杭州", auto_search=False)
    with pytest.raises(ValueError):
        patch_job_search_card(current, patch)
    assert current == expected


def test_patch_api_translates_invalid_payload_into_422(setup_runner, monkeypatch):
    state = setup_runner.prepare("你好", prior_status="editing")
    monkeypatch.setattr(career_router, "get_career_read_services", lambda request: SimpleNamespace(conversation_repository=state.repository))
    monkeypatch.setattr(career_router, "get_request_actor", lambda: SimpleNamespace(actor_id=state.active.conversation.actor_id))
    with pytest.raises(career_router.HTTPException) as error:
        asyncio.run(career_router.update_job_search_card(
            state.active.conversation.id, state.card.id, {"conditions": {"city": []}}, None))
    assert error.value.status_code == 422
