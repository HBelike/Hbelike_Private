"""对话求职的入队快照、内部续答和历史事实边界。"""

from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from src.career_assistant.contracts import CareerInboundMessage, ModelSelectionMode
from src.career_assistant.conversation_memory import group_complete_turns, ConversationMemoryService, ConversationSummary
from src.career_assistant.persistence.records import MessageRecord, MessageRole, AgentTurnRecord, AgentTurnStatus
from src.career_assistant.turn_payloads import prepare_turn_payload, restore_turn_input
from src.career_assistant.job_search_cards import new_job_search_card, patch_job_search_card
import pytest


def test_turn_roundtrip_keeps_frozen_search_references_and_source():
    now = datetime.now(UTC)
    inbound = CareerInboundMessage(uuid4(), uuid4(), uuid4(), "比较两批岗位",
        job_search_snapshot={"schema_version": 1, "catalog": [{"id": "batch-A", "revision": 3}]},
        execution_source="job_search_auto_analysis", parent_turn_id=uuid4(), job_search_batch_id=uuid4())
    turn = AgentTurnRecord(inbound.turn_id, inbound.conversation_id, inbound.actor_id,
        ModelSelectionMode.PLATFORM_DEFAULT, None, ("job_search_auto_analysis",), AgentTurnStatus.RUNNING,
        None, None, now, None, now, now)
    payload = prepare_turn_payload(inbound, ())
    restored = restore_turn_input(turn, payload).inbound_message
    assert restored.job_search_snapshot == inbound.job_search_snapshot
    assert restored.execution_source == "job_search_auto_analysis"
    assert restored.parent_turn_id == inbound.parent_turn_id
    assert restored.job_search_batch_id == inbound.job_search_batch_id


def test_automatic_assistant_without_fake_user_is_preserved_in_history():
    now = datetime.now(UTC)
    message = MessageRecord(uuid4(), uuid4(), uuid4(), MessageRole.ASSISTANT,
        "第1批网易岗位推荐：Agent开发", False, now,
        ui_payload={"type": "job_search_analysis", "batch_id": str(uuid4())})
    turns = group_complete_turns([message])
    assert len(turns) == 1
    assert turns[0].user_message is None
    assert turns[0].assistant_message == message
    prompt = ConversationMemoryService._compaction_messages(ConversationSummary.empty(), turns)
    assert "第1批网易岗位推荐" in str(prompt)
    ordinary = replace(message, ui_payload=None)
    assert group_complete_turns([ordinary]) == ()


def test_v2_projection_allows_confirmation_without_overwriting_batch_facts():
    card = {**new_job_search_card("网易云", "杭州", auto_search=True), "version": 2}
    result = patch_job_search_card(card, {"status": "selected", "selected_job": {"id": "12", "title": "Agent工程师"}})
    assert result["status"] == "selected"
    for patch in ({"conditions": {"keyword": "腾讯"}}, {"result_summary": {"count": 0, "jobs": []}}):
        with pytest.raises(ValueError, match="搜索批次"):
            patch_job_search_card(card, patch)


def test_automatic_analysis_model_unavailable_is_failed_and_can_be_retried():
    from src.career_assistant.response_runner import CareerResponseRunner
    active = SimpleNamespace(source="job_search_auto_analysis", parent_turn_id=uuid4(), job_search_batch_id=uuid4(),
        conversation=SimpleNamespace(actor_id=uuid4(), id=uuid4()), turn=SimpleNamespace(id=uuid4()))
    failed = SimpleNamespace(status=AgentTurnStatus.FAILED)
    loop = SimpleNamespace(repository=SimpleNamespace(append_message=Mock(return_value=Mock())),
                           mark_turn_failed=Mock(return_value=failed), mark_turn_succeeded=Mock())
    runner = CareerResponseRunner(loop, Mock(), redactor=SimpleNamespace(enabled=False, redact=lambda text: text))
    result = runner._finish_with_advisory(active, "平台默认模型暂不可用", None)
    assert result.turn.status is AgentTurnStatus.FAILED
    loop.mark_turn_succeeded.assert_not_called()
    assert loop.mark_turn_failed.call_args.args[2] == "job_search_model_unavailable"
