"""只有本次创建时间附近的显式源消息 ID 才能作为接受证据；只用本机隔离 PostgreSQL。"""

from datetime import timedelta
from uuid import UUID

import pytest

from test_external_chat import NOW, local_database, store
from test_external_chat_execution import create, execution, insert_source, transition
from src.career_assistant.persistence.external_chat_execution_repository import ExternalChatExecutionError


def _submitted(context, *, created_at):
    operation_id = UUID(create(context, now=created_at)["client_operation_id"])
    for status in ("executing", "submitting", "submitted"):
        transition(context, operation_id, status, now=created_at)
    return operation_id


@pytest.mark.parametrize("source_at", [
    NOW - timedelta(days=1),
    NOW - timedelta(seconds=1),
    NOW - timedelta(microseconds=1),
])
def test_historical_matching_source_id_cannot_accept_new_operation(execution, source_at):
    created_at = NOW + timedelta(microseconds=750000)
    operation_id = _submitted(execution, created_at=created_at)
    insert_source(execution, "previous-matching-text", sent_at=source_at)
    with pytest.raises(ExternalChatExecutionError) as captured:
        transition(execution, operation_id, "accepted", platform_message_id="previous-matching-text", now=created_at)
    assert captured.value.code == "evidence_missing"
    repository, org, actor, *_ = execution
    operation = repository.get_operation(org, actor, operation_id, now=created_at)["operation"]
    assert operation["status"] == "submitted"
    assert operation["platform_message_id"] is None


@pytest.mark.parametrize("microseconds", [0, 750000, 999999])
def test_source_second_precision_allows_less_than_one_second_rounding(execution, microseconds):
    created_at = NOW + timedelta(microseconds=microseconds)
    operation_id = _submitted(execution, created_at=created_at)
    insert_source(execution, "this-second-explicit-mid", sent_at=NOW)
    accepted = transition(execution, operation_id, "accepted", platform_message_id="this-second-explicit-mid", now=created_at)["operation"]
    assert accepted["status"] == "accepted"
    assert accepted["platform_message_id"] == "this-second-explicit-mid"


def test_one_second_earlier_evidence_is_outside_rounding_window(execution):
    created_at = NOW + timedelta(microseconds=999999)
    operation_id = _submitted(execution, created_at=created_at)
    insert_source(execution, "one-second-earlier", sent_at=created_at - timedelta(seconds=1))
    with pytest.raises(ExternalChatExecutionError, match="源消息证据"):
        transition(execution, operation_id, "accepted", platform_message_id="one-second-earlier", now=created_at)
