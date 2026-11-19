"""OpenAI-compatible 模型客户端结束原因测试。"""

import httpx
import pytest

from src.career_assistant.model_clients import (
    ChatMessage,
    FunctionToolDefinition,
    ModelConnectionTarget,
    ModelInvocationError,
    OpenAICompatibleChatClient,
    normalize_model_connection_target,
)


TARGET = ModelConnectionTarget(
    provider_key="custom",
    model_id="test-model",
    api_base_url="https://example.test/v1",
)


def test_official_moonshot_custom_target_normalizes_kimi_k3_alias() -> None:
    target = normalize_model_connection_target(
        ModelConnectionTarget("custom", "kimi K3", "https://api.moonshot.cn/v1"),
    )

    assert target == ModelConnectionTarget(
        provider_key="kimi",
        model_id="kimi-k3",
        api_base_url="https://api.moonshot.cn/v1",
    )


def test_unrelated_custom_target_keeps_model_id_unchanged() -> None:
    target = normalize_model_connection_target(
        ModelConnectionTarget("custom", "Vendor Model", "https://example.test/v1"),
    )

    assert target == ModelConnectionTarget(
        provider_key="custom",
        model_id="Vendor Model",
        api_base_url="https://example.test/v1",
    )


def test_kimi_k3_uses_completion_tokens_and_fixed_sampling_contract() -> None:
    payload = OpenAICompatibleChatClient._build_request_payload(
        ModelConnectionTarget("custom", "kimi K3", "https://api.moonshot.cn/v1"),
        [ChatMessage("user", "你好")],
        max_tokens=512,
    )

    assert payload["model"] == "kimi-k3"
    assert payload["max_completion_tokens"] == 512
    assert payload["reasoning_effort"] == "low"
    assert "max_tokens" not in payload
    assert "temperature" not in payload


def test_kimi_k2_6_omits_fixed_sampling_temperature() -> None:
    payload = OpenAICompatibleChatClient._build_request_payload(
        ModelConnectionTarget("kimi", "kimi-k2.6", "https://api.moonshot.cn/v1"),
        [ChatMessage("user", "你好")],
        max_tokens=512,
    )

    assert payload["model"] == "kimi-k2.6"
    assert payload["max_tokens"] == 512
    assert "temperature" not in payload
    assert "reasoning_effort" not in payload


def test_kimi_k3_custom_connection_reaches_official_chat_endpoint() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["payload"] = request.read().decode("utf-8")
        return httpx.Response(
            200,
            json={
                "model": "kimi-k3",
                "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            },
        )

    client = OpenAICompatibleChatClient(
        httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = client.test_connection(
        ModelConnectionTarget("custom", "kimi K3", "https://api.moonshot.cn/v1"),
        "test-key",
    )

    assert result == "OK"
    assert captured["url"] == "https://api.moonshot.cn/v1/chat/completions"
    assert '"model":"kimi-k3"' in str(captured["payload"])
    assert '"max_completion_tokens":512' in str(captured["payload"])


def test_kimi_k2_6_connection_reaches_official_chat_endpoint_without_temperature() -> None:
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["payload"] = request.read().decode("utf-8")
        return httpx.Response(
            200,
            json={
                "model": "kimi-k2.6",
                "choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 2},
            },
        )

    client = OpenAICompatibleChatClient(
        httpx.Client(transport=httpx.MockTransport(handler)),
    )

    result = client.test_connection(
        ModelConnectionTarget("kimi", "kimi-k2.6", "https://api.moonshot.cn/v1"),
        "test-key",
    )

    assert result == "OK"
    assert captured["url"] == "https://api.moonshot.cn/v1/chat/completions"
    assert '"model":"kimi-k2.6"' in str(captured["payload"])
    assert '"max_tokens":512' in str(captured["payload"])
    assert '"temperature"' not in str(captured["payload"])


def test_non_stream_length_response_keeps_text_and_adds_notice() -> None:
    client = OpenAICompatibleChatClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "finish_reason": "length",
                                "message": {"content": "未完成正文"},
                            },
                        ],
                    },
                ),
            ),
        ),
    )

    result = client._request_completion(
        TARGET,
        "test-key",
        [ChatMessage("user", "请回答")],
        max_tokens=100,
    )

    assert result.startswith("未完成正文")
    assert "达到模型输出上限" in result


def test_stream_length_response_adds_notice_after_existing_chunks() -> None:
    client = OpenAICompatibleChatClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    content=(
                        b'data: {"choices":[{"delta":{"content":"part"}}]}\n\n'
                        b'data: {"choices":[{"finish_reason":"length","delta":{}}]}\n\n'
                        b"data: [DONE]\n\n"
                    ),
                ),
            ),
        ),
    )

    result = "".join(
        client._stream_request_completion(
            TARGET,
            "test-key",
            [ChatMessage("user", "请回答")],
            max_tokens=100,
        ),
    )

    assert result.startswith("part")
    assert "达到模型输出上限" in result


def test_truncated_tool_call_is_rejected_before_execution() -> None:
    client = OpenAICompatibleChatClient(
        httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "choices": [
                            {
                                "finish_reason": "length",
                                "message": {
                                    "content": None,
                                    "tool_calls": [
                                        {
                                            "id": "call-1",
                                            "function": {
                                                "name": "inspect",
                                                "arguments": '{"url":"unterminated',
                                            },
                                        },
                                    ],
                                },
                            },
                        ],
                    },
                ),
            ),
        ),
    )

    with pytest.raises(ModelInvocationError, match="Tool Calling 达到输出上限"):
        client._request_tool_completion(
            TARGET,
            "test-key",
            [ChatMessage("user", "检查")],
            (FunctionToolDefinition("inspect", "检查", {"type": "object"}),),
            tool_choice="auto",
            max_tokens=100,
        )
