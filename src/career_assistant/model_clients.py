"""面向免费额度 Provider 的 OpenAI-compatible 文本调用器。"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from src.career_assistant.persistence.model_profile_repository import ModelProfileRecord
from src.observability.langsmith_runtime import trace_operation, trace_stream


class ModelInvocationError(RuntimeError):
    """不会包含 Provider 响应正文或密钥的安全模型调用异常。"""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


@dataclass(frozen=True)
class ChatMessage:
    """单条 OpenAI-compatible ChatCompletions 消息。"""

    role: str
    content: str | list[dict[str, object]]


@dataclass(frozen=True)
class ModelConnectionTarget:
    """连通性测试需要的最小模型连接信息，不含数据库记录或 API Key。"""

    provider_key: str
    model_id: str
    api_base_url: str | None


class OpenAICompatibleChatClient:
    """统一调用 Groq、OpenRouter 与 Gemini OpenAI-compatible 端点。"""

    _DEFAULT_BASE_URLS = {
        "deepseek": "https://api.deepseek.com",
        "groq": "https://api.groq.com/openai/v1",
        "openrouter": "https://openrouter.ai/api/v1",
        "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
        "modelscope": "https://api-inference.modelscope.cn/v1",
        "siliconflow": "https://api.siliconflow.cn/v1",
        "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "baidu-qianfan": "https://qianfan.baidubce.com/v2",
        "hunyuan": "https://api.hunyuan.cloud.tencent.com/v1",
        "nvidia": "https://integrate.api.nvidia.com/v1",
        "tokenhub": "https://tokenhub.tencentmaas.com/v1",
    }
    _DEFAULT_CONNECTION_TIMEOUT_SECONDS = 90.0
    _DEFAULT_COMPLETION_MAX_TOKENS = 4_096

    def __init__(
        self,
        client: httpx.Client | None = None,
        *,
        completion_max_tokens: int = _DEFAULT_COMPLETION_MAX_TOKENS,
        request_timeout_seconds: float = _DEFAULT_CONNECTION_TIMEOUT_SECONDS,
    ) -> None:
        """复用进程级 HTTP 连接，避免每轮对话重复进行 TCP/TLS 握手。"""

        if (
            isinstance(completion_max_tokens, bool)
            or not isinstance(completion_max_tokens, int)
            or completion_max_tokens <= 0
        ):
            raise ValueError("模型单次最大输出 Token 数必须大于 0")
        if (
            isinstance(request_timeout_seconds, bool)
            or not isinstance(request_timeout_seconds, int | float)
            or request_timeout_seconds <= 0
        ):
            raise ValueError("模型请求超时时间必须大于 0")

        self._completion_max_tokens = completion_max_tokens
        self._request_timeout_seconds = float(request_timeout_seconds)
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(self._request_timeout_seconds),
        )

    def close(self) -> None:
        """在应用退出时关闭本实例创建的 HTTP 连接池。"""

        if self._owns_client:
            self._client.close()

    def complete(
        self,
        profile: ModelProfileRecord,
        credential_env_name: str | None,
        messages: list[ChatMessage],
        *,
        api_key: str | None = None,
    ) -> str:
        """执行一次非流式调用；API Key 仅在当前请求内存中短暂存在。"""

        credential = (api_key or "").strip()
        if not credential and credential_env_name:
            credential = os.getenv(credential_env_name, "").strip()
        if not credential:
            raise ModelInvocationError("免费模型额度凭证尚未配置")
        target = ModelConnectionTarget(
            provider_key=profile.provider_key,
            model_id=profile.model_id,
            api_base_url=profile.api_base_url,
        )
        return trace_operation(
            run_name="career.chat.completion",
            run_type="llm",
            execute=lambda: self._request_completion(
                target,
                credential,
                messages,
                max_tokens=self._completion_max_tokens,
            ),
            summarize=lambda result: {
                "output_characters": len(result),
                "has_output": bool(result),
            },
            metadata={
                **self._trace_metadata(target, streaming=False),
                **self._trace_message_metrics(messages),
            },
            tags=("career", "chat", "privacy:metadata-only"),
        )

    def test_connection(
        self,
        target: ModelConnectionTarget,
        api_key: str,
    ) -> str:
        """发起极小的真实推理请求，验证地址、模型 ID 与 Key 能共同工作。

        使用一条可读的固定提问，要求模型回复 ``OK``。返回值只供调用方展示
        短预览，不持久化模型响应，也不把 API Key 写入日志。
        """

        credential = api_key.strip()
        if not credential:
            raise ModelInvocationError("请填写 API Key 后再测试连接")
        messages = [
            ChatMessage(role="system", content="你正在进行 API 连通性验证。"),
            ChatMessage(role="user", content="你好，请仅回复 OK。"),
        ]
        return trace_operation(
            run_name="career.model.connectivity_test",
            run_type="llm",
            execute=lambda: self._request_completion(
                target,
                credential,
                messages,
                # 推理模型可能先产生内部 reasoning，再输出最终 content。4 个 token
                # 会让这类模型只来得及开始推理，进而被误判为“无返回内容”。
                max_tokens=96,
            ),
            summarize=lambda result: {
                "output_characters": len(result),
                "has_output": bool(result),
            },
            metadata={
                **self._trace_metadata(target, streaming=False),
                **self._trace_message_metrics(messages),
                "operation": "connectivity_test",
            },
            tags=("career", "chat", "connectivity-test", "privacy:metadata-only"),
        )

    def stream_complete(
        self,
        profile: ModelProfileRecord,
        credential_env_name: str | None,
        messages: list[ChatMessage],
        *,
        api_key: str | None = None,
    ) -> Iterator[str]:
        """以 OpenAI-compatible SSE 协议逐段产出正文，不输出 reasoning 内容。"""

        credential = (api_key or "").strip()
        if not credential and credential_env_name:
            credential = os.getenv(credential_env_name, "").strip()
        if not credential:
            raise ModelInvocationError("模型 API Key 尚未配置")
        target = ModelConnectionTarget(
            provider_key=profile.provider_key,
            model_id=profile.model_id,
            api_base_url=profile.api_base_url,
        )
        yield from trace_stream(
            run_name="career.chat.stream",
            run_type="llm",
            execute=lambda: self._stream_request_completion(
                target,
                credential,
                messages,
                max_tokens=self._completion_max_tokens,
            ),
            summarize_chunk=lambda chunk, index: {
                "chunk_index": index,
                "output_characters": len(chunk),
            },
            metadata={
                **self._trace_metadata(target, streaming=True),
                **self._trace_message_metrics(messages),
            },
            tags=("career", "chat", "stream", "privacy:metadata-only"),
        )

    @classmethod
    def _trace_message_metrics(
        cls,
        messages: list[ChatMessage],
    ) -> dict[str, object]:
        """构造不包含对话正文、附件 Data URL 或凭证的规模摘要。"""

        return {
            "message_count": len(messages),
            "input_characters": sum(cls._message_character_count(item) for item in messages),
        }

    @staticmethod
    def _trace_metadata(
        target: ModelConnectionTarget,
        *,
        streaming: bool,
    ) -> dict[str, object]:
        """只记录定位 Provider 调用所需的非敏感维度。"""

        return {
            "provider": target.provider_key,
            "model": target.model_id,
            "streaming": streaming,
            "privacy_mode": "metadata_only",
        }

    @staticmethod
    def _message_character_count(message: ChatMessage) -> int:
        """统计输入规模；多模态 Data URL 只计长度，不返回任何内容。"""

        if isinstance(message.content, str):
            return len(message.content)
        count = 0
        for part in message.content:
            text = part.get("text")
            if isinstance(text, str):
                count += len(text)
            image_url = part.get("image_url")
            if isinstance(image_url, dict):
                url = image_url.get("url")
                if isinstance(url, str):
                    count += len(url)
        return count

    def _request_completion(
        self,
        target: ModelConnectionTarget,
        credential: str,
        messages: list[ChatMessage],
        *,
        max_tokens: int,
    ) -> str:
        """统一发送 OpenAI-compatible ChatCompletions 请求并清洗异常。"""

        base_url = self._base_url_for(target)
        try:
            response = self._client.post(
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {credential}",
                    "Content-Type": "application/json",
                },
                json=self._build_request_payload(
                    target,
                    messages,
                    max_tokens=max_tokens,
                ),
            )
        except httpx.TimeoutException as exc:
            raise ModelInvocationError(
                f"连接模型服务超时（{self._request_timeout_seconds:g} 秒）。请确认 API 地址可访问后重试。",
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise ModelInvocationError(
                "无法访问模型服务。请检查 API Base URL、网络连接和服务商状态。",
                retryable=True,
            ) from exc
        if response.status_code >= 400:
            raise ModelInvocationError(
                self._http_error_message(response.status_code),
                retryable=self._is_retryable_http_status(response.status_code),
            )
        try:
            payload = response.json()
            choice = payload["choices"][0]
            message = choice["message"]
            content = message["content"]
        except (IndexError, KeyError, TypeError, ValueError) as exc:
            raise ModelInvocationError("模型服务返回格式异常") from exc
        if isinstance(content, str) and content.strip():
            return content.strip()
        if isinstance(content, list):
            text_parts = [
                item.get("text", "").strip()
                for item in content
                if isinstance(item, dict) and isinstance(item.get("text"), str)
            ]
            joined_text = "\n".join(part for part in text_parts if part)
            if joined_text:
                return joined_text
        if isinstance(message, dict) and message.get("reasoning_content"):
            finish_reason = choice.get("finish_reason") if isinstance(choice, dict) else None
            if finish_reason == "length":
                raise ModelInvocationError(
                    "模型已开始推理，但测试输出额度不足以生成最终回复。请重试；若仍出现，请更换非推理模型。",
                )
        raise ModelInvocationError("模型没有生成可读取的文本回复，请检查模型 ID 是否支持 Chat Completions")

    def _stream_request_completion(
        self,
        target: ModelConnectionTarget,
        credential: str,
        messages: list[ChatMessage],
        *,
        max_tokens: int,
    ) -> Iterator[str]:
        """解析上游 SSE 数据帧，并只转发可展示的 content 增量。"""

        base_url = self._base_url_for(target)
        yielded_content = False
        try:
            with self._client.stream(
                "POST",
                f"{base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {credential}",
                    "Content-Type": "application/json",
                },
                json=self._build_request_payload(
                    target,
                    messages,
                    max_tokens=max_tokens,
                    stream=True,
                ),
            ) as response:
                if response.status_code >= 400:
                    raise ModelInvocationError(
                        self._http_error_message(response.status_code),
                        retryable=self._is_retryable_http_status(response.status_code),
                    )
                for line in response.iter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        payload = json.loads(data)
                        choice = payload["choices"][0]
                        delta = choice.get("delta", {})
                        content = delta.get("content")
                    except (IndexError, KeyError, TypeError, ValueError) as exc:
                        raise ModelInvocationError(
                            "模型流式响应格式异常",
                        ) from exc
                    text = self._content_text(content)
                    if text:
                        yielded_content = True
                        yield text
        except httpx.TimeoutException as exc:
            raise ModelInvocationError(
                f"连接模型服务超时（{self._request_timeout_seconds:g} 秒）。请确认 API 地址可访问后重试。",
                retryable=True,
            ) from exc
        except httpx.RequestError as exc:
            raise ModelInvocationError(
                "无法访问模型服务。请检查 API Base URL、网络连接和服务商状态。",
                retryable=True,
            ) from exc

        if not yielded_content:
            raise ModelInvocationError(
                "模型没有生成可读取的流式文本，请检查模型 ID 是否支持流式 Chat Completions",
            )

    @staticmethod
    def _content_text(content: object) -> str:
        """兼容文本字符串与分段文本数组两种 OpenAI-compatible 内容格式。"""

        if isinstance(content, str):
            return content
        if isinstance(content, list):
            return "\n".join(
                item.get("text", "").strip()
                for item in content
                if isinstance(item, dict) and isinstance(item.get("text"), str)
            )
        return ""

    @staticmethod
    def _build_request_payload(
        target: ModelConnectionTarget,
        messages: list[ChatMessage],
        *,
        max_tokens: int,
        stream: bool = False,
    ) -> dict[str, object]:
        """构造不包含凭证的 Chat Completions 请求体。

        DeepSeek V4-Pro 默认会先输出较长的内部推理。求职助手的常规问答、
        简历初评与岗位匹配更重视首字延迟，因此统一显式关闭该模式；复杂推理
        应在未来以独立的用户可见模式开启，而不是隐式拖慢每一轮聊天。
        """

        payload: dict[str, object] = {
            "model": target.model_id,
            "messages": [
                {"role": message.role, "content": message.content}
                for message in messages
            ],
            "temperature": 0.25,
            "max_tokens": max_tokens,
        }
        if stream:
            payload["stream"] = True
        if (
            target.provider_key == "deepseek"
            and target.model_id.lower().startswith("deepseek-v4")
        ):
            payload["thinking"] = {"type": "disabled"}
        return payload

    def _base_url_for(self, target: ModelConnectionTarget) -> str:
        """优先使用页面配置的兼容端点，内置 Provider 仅作为安全默认值。"""

        configured_url = (target.api_base_url or "").strip().rstrip("/")
        if configured_url:
            if not configured_url.startswith("https://"):
                raise ModelInvocationError("模型 API 地址必须使用 HTTPS")
            if target.provider_key == "deepseek" and urlparse(configured_url).netloc.lower() == "platform.deepseek.com":
                # 兼容旧版页面把控制台网址误存为 API 地址的记录；真正的请求仍走官方 API。
                return self._DEFAULT_BASE_URLS["deepseek"]
            return configured_url
        default_url = self._DEFAULT_BASE_URLS.get(target.provider_key)
        if default_url is None:
            raise ModelInvocationError("该 Provider 需要在模型档案中配置 OpenAI-compatible API 地址")
        return default_url

    @staticmethod
    def _http_error_message(status_code: int) -> str:
        """将上游 HTTP 状态转成不泄露密钥的可操作提示。"""

        if status_code == 400:
            return "服务商未接受请求。请检查模型 ID 是否属于所选服务商，以及 API Base URL 是否正确。"
        if status_code == 401:
            return "API Key 无效、已失效，或与当前服务商不匹配。请重新创建并粘贴该服务商的 Key。"
        if status_code == 403:
            return "当前 API Key 没有调用此模型的权限。请检查服务商控制台中的权限或账户状态。"
        if status_code == 404:
            return "未找到调用接口或模型。请检查 API Base URL 和模型 ID；不要将服务商官网或控制台地址填入 API Base URL。"
        if status_code == 429:
            return "服务商已限制本次请求，可能是免费额度用尽或请求过于频繁。请稍后重试或更换免费模型。"
        if 500 <= status_code <= 599:
            return "模型服务暂时异常，请稍后重试。"
        return f"模型服务拒绝了本次请求（状态码 {status_code}）。请检查模型配置后重试。"

    @staticmethod
    def _is_retryable_http_status(status_code: int) -> bool:
        """只把短暂拥塞视为可重试，避免重试鉴权或参数错误。"""

        return status_code in {408, 409, 425, 429} or 500 <= status_code <= 599
