"""模型上下文策略测试。"""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from src.career_assistant.contracts import ModelCapability
from src.career_assistant.persistence.model_profile_repository import (
    CareerModelProfileRepository,
    ModelContextPolicy,
)


def test_policy_rejects_target_not_below_trigger() -> None:
    policy = ModelContextPolicy(
        context_window_tokens=64_000,
        reserved_output_tokens=4_096,
        compression_trigger_percent=80,
        compression_target_percent=80,
        context_window_source="admin",
    )

    with pytest.raises(ValueError, match="目标比例"):
        policy.validate()


def test_policy_rejects_output_larger_than_half_context() -> None:
    policy = ModelContextPolicy(
        context_window_tokens=8_192,
        reserved_output_tokens=4_097,
    )

    with pytest.raises(ValueError, match="预留输出"):
        policy.validate()


def test_record_reads_context_policy_with_backward_compatible_defaults() -> None:
    now = datetime.now(UTC)
    row = {
        "id": uuid4(),
        "organization_id": uuid4(),
        "profile_key": "test-model",
        "display_name": "测试模型",
        "provider_key": "openai-compatible",
        "model_id": "test-model",
        "api_base_url": None,
        "provider_website_url": None,
        "capability_codes": [ModelCapability.TEXT.value],
        "cost_tier": "paid",
        "enabled": True,
        "priority": 100,
        "created_at": now,
        "updated_at": now,
    }

    record = CareerModelProfileRepository._to_record(row)

    assert record.context_policy == ModelContextPolicy()


def test_admin_context_policy_is_read_from_row() -> None:
    now = datetime.now(UTC)
    row = {
        "id": uuid4(),
        "organization_id": uuid4(),
        "profile_key": "large-model",
        "display_name": "大上下文模型",
        "provider_key": "openai-compatible",
        "model_id": "large-model",
        "api_base_url": None,
        "provider_website_url": None,
        "capability_codes": [ModelCapability.TEXT.value],
        "cost_tier": "paid",
        "enabled": True,
        "priority": 100,
        "created_at": now,
        "updated_at": now,
        "context_window_tokens": 128_000,
        "reserved_output_tokens": 8_192,
        "compression_trigger_percent": 80,
        "compression_target_percent": 60,
        "context_window_source": "admin",
    }

    record = CareerModelProfileRepository._to_record(row)

    assert record.context_policy.context_window_tokens == 128_000
    assert record.context_policy.context_window_source == "admin"
