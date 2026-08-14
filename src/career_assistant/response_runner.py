"""求职助手的模型路由、回复生成与 Turn 收口服务。"""

from __future__ import annotations

import time
from dataclasses import dataclass

from src.career_assistant.agent_loop import ActiveAgentTurn, CareerAgentLoop
from src.career_assistant.contracts import CareerInboundMessage, ModelSelectionRequest
from src.career_assistant.intake_graph import IntakeGraphResult, ModelTurnContext
from src.career_assistant.model_clients import (
    ChatMessage,
    ModelInvocationError,
    OpenAICompatibleChatClient,
)
from src.career_assistant.model_gateway import ModelGateway, ModelReadiness, ModelResolution
from src.career_assistant.persistence import AgentTurnRecord, MessageRecord, MessageRole
from src.career_assistant.privacy import SensitiveDataRedactor


@dataclass(frozen=True)
class CareerResponseResult:
    """回复执行结果，供 Web 层同时渲染用户消息、助手消息和 Turn 状态。"""

    assistant_message: MessageRecord
    turn: AgentTurnRecord
    model_resolution: ModelResolution | None


@dataclass(frozen=True)
class CareerResponseStreamEvent:
    """一条流式回复事件；仅在本次请求内存在，不参与数据库持久化。"""

    event_type: str
    content: str | None = None
    result: CareerResponseResult | None = None


class CareerResponseRunner:
    """在输入图完成后执行模型路由，并确保每一轮 Turn 都有最终状态。"""

    def __init__(
        self,
        agent_loop: CareerAgentLoop,
        model_gateway: ModelGateway,
        chat_client: OpenAICompatibleChatClient | None = None,
        redactor: SensitiveDataRedactor | None = None,
        *,
        max_persisted_response_characters: int = 30_000,
        max_attempts: int = 1,
        retry_backoff_seconds: float = 0.8,
    ) -> None:
        if (
            isinstance(max_persisted_response_characters, bool)
            or not isinstance(max_persisted_response_characters, int)
            or not 1 <= max_persisted_response_characters <= 30_000
        ):
            raise ValueError("助手回复持久化字符上限必须在 1 到 30000 之间")
        if isinstance(max_attempts, bool) or not isinstance(max_attempts, int) or not 1 <= max_attempts <= 2:
            raise ValueError("模型生成最多只能尝试 1 到 2 次")
        if isinstance(retry_backoff_seconds, bool) or not isinstance(retry_backoff_seconds, int | float) or retry_backoff_seconds <= 0:
            raise ValueError("模型生成重试等待时间必须大于 0")
        self._agent_loop = agent_loop
        self._model_gateway = model_gateway
        self._chat_client = chat_client or OpenAICompatibleChatClient()
        self._redactor = redactor or SensitiveDataRedactor()
        self._max_persisted_response_characters = max_persisted_response_characters
        self._max_attempts = max_attempts
        self._retry_backoff_seconds = float(retry_backoff_seconds)

    @staticmethod
    def _model_resolution_label(resolution: ModelResolution) -> str:
        """用真实 Model ID 区分同一服务商下的多个连接。"""

        display_name = resolution.profile.display_name.strip()
        model_id = resolution.profile.model_id.strip()
        if not display_name or display_name.casefold() == model_id.casefold():
            return model_id
        return f"{display_name} · {model_id}"

    def run(
        self,
        inbound_message: CareerInboundMessage,
        intake_result: IntakeGraphResult,
    ) -> CareerResponseResult:
        """解析本轮模型，调用可用免费额度，或生成不伪装为分析结果的配置提示。"""

        active_turn = intake_result.active_turn
        selection = ModelSelectionRequest(
            mode=inbound_message.model_selection.mode,
            profile_id=inbound_message.model_selection.profile_id,
            required_capabilities=intake_result.model_context.required_capabilities,
        )
        try:
            resolution = self._model_gateway.resolve(
                active_turn.conversation.organization_id,
                selection,
            )
        except (LookupError, PermissionError, ValueError):
            return self._finish_with_advisory(
                active_turn,
                "已安全接收本轮材料。当前没有可用的免费模型档案，暂不生成匹配结论。"
                "请在“模型设置”登记支持本轮能力的免费额度模型后重试；原始文件已删除，"
                "历史中仅保留脱敏摘要。",
                None,
            )

        if resolution.readiness is not ModelReadiness.READY:
            return self._finish_with_advisory(
                active_turn,
                f"已安全接收本轮材料。已选择“{self._model_resolution_label(resolution)}”，"
                "但其免费额度凭证尚未在服务端配置，因此本轮不会伪造分析结论。"
                "原始文件已删除，历史中仅保留脱敏摘要。",
                resolution,
            )

        try:
            generated_reply = self._complete_with_retry(
                resolution,
                self._build_prompt(active_turn, intake_result.model_context),
            )
        except ModelInvocationError as exc:
            return self._finish_with_model_failure(active_turn, resolution, exc)

        if not generated_reply.strip():
            return self._finish_with_model_failure(
                active_turn,
                resolution,
                ModelInvocationError("模型未返回可用文本，请检查模型连接或更换模型后重试。"),
            )

        message = self._append_assistant_message(active_turn, generated_reply)
        succeeded_turn = self._agent_loop.mark_turn_succeeded(
            active_turn.conversation.actor_id,
            active_turn.turn.id,
        )
        return CareerResponseResult(message, succeeded_turn, resolution)

    def stream(
        self,
        inbound_message: CareerInboundMessage,
        intake_result: IntakeGraphResult,
    ):
        """真实转发模型增量，并只在流结束时写入一条完整的助手消息。"""

        active_turn = intake_result.active_turn
        selection = ModelSelectionRequest(
            mode=inbound_message.model_selection.mode,
            profile_id=inbound_message.model_selection.profile_id,
            required_capabilities=intake_result.model_context.required_capabilities,
        )
        try:
            resolution = self._model_gateway.resolve(
                active_turn.conversation.organization_id,
                selection,
            )
        except (LookupError, PermissionError, ValueError):
            yield CareerResponseStreamEvent(
                event_type="done",
                result=self._finish_with_advisory(
                    active_turn,
                    "已安全接收本轮材料，但当前没有可用的模型连接，因此暂不生成分析结论。"
                    "请在“模型与连接”中配置支持本轮能力的模型后重试。",
                    None,
                ),
            )
            return

        if resolution.readiness is not ModelReadiness.READY:
            yield CareerResponseStreamEvent(
                event_type="done",
                result=self._finish_with_advisory(
                    active_turn,
                    f"已选择“{self._model_resolution_label(resolution)}”，但其 API Key 尚未可用，"
                    "因此本轮不会伪造分析结论。请在“模型与连接”中重新保存后重试。",
                    resolution,
                ),
            )
            return

        generated_parts: list[str] = []
        prompt = self._build_prompt(active_turn, intake_result.model_context)
        try:
            for attempt in range(1, self._max_attempts + 1):
                try:
                    for content in self._chat_client.stream_complete(
                        resolution.profile,
                        resolution.credential_env_name,
                        prompt,
                        api_key=resolution.credential,
                    ):
                        generated_parts.append(content)
                        yield CareerResponseStreamEvent(event_type="delta", content=content)
                    break
                except ModelInvocationError as exc:
                    # 一旦浏览器收到正文就不能重放，避免同一问题出现两份回答。
                    if generated_parts or not exc.retryable or attempt >= self._max_attempts:
                        raise
                    yield CareerResponseStreamEvent(
                        event_type="progress",
                        content=f"模型服务暂时不可用，正在自动重试（{attempt}/{self._max_attempts}）…",
                    )
                    time.sleep(self._retry_backoff_seconds)
        except ModelInvocationError as exc:
            yield CareerResponseStreamEvent(
                event_type="error",
                result=self._finish_with_model_failure(active_turn, resolution, exc),
            )
            return
        except Exception:
            yield CareerResponseStreamEvent(
                event_type="error",
                result=self._finish_with_model_failure(
                    active_turn,
                    resolution,
                    ModelInvocationError("模型生成过程中发生未预期错误，请稍后重试。"),
                ),
            )
            return

        generated_reply = "".join(generated_parts).strip()
        if not generated_reply:
            yield CareerResponseStreamEvent(
                event_type="error",
                result=self._finish_with_model_failure(
                    active_turn,
                    resolution,
                    ModelInvocationError(
                        "模型未返回可用文本，请检查模型连接或更换模型后重试。",
                    ),
                ),
            )
            return

        message = self._append_assistant_message(active_turn, generated_reply)
        succeeded_turn = self._agent_loop.mark_turn_succeeded(
            active_turn.conversation.actor_id,
            active_turn.turn.id,
        )
        yield CareerResponseStreamEvent(
            event_type="done",
            result=CareerResponseResult(message, succeeded_turn, resolution),
        )

    def _complete_with_retry(
        self,
        resolution: ModelResolution,
        prompt: list[ChatMessage],
    ) -> str:
        """让普通 HTTP 路径也遵守同一套受控重试规则。"""

        last_error: ModelInvocationError | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                return self._chat_client.complete(
                    resolution.profile,
                    resolution.credential_env_name,
                    prompt,
                    api_key=resolution.credential,
                )
            except ModelInvocationError as exc:
                last_error = exc
                if not exc.retryable or attempt >= self._max_attempts:
                    raise
                time.sleep(self._retry_backoff_seconds)
        if last_error is None:
            raise RuntimeError("模型重试未产生调用结果")
        raise last_error

    def _finish_with_model_failure(
        self,
        active_turn: ActiveAgentTurn,
        resolution: ModelResolution,
        error: ModelInvocationError,
    ) -> CareerResponseResult:
        """将安全的模型错误落库，避免前端把失败误显示为生成成功。"""

        public_error = str(error).strip() or "模型服务未返回可用结果"
        failure_message = f"模型调用未完成：{public_error}"
        failed_turn = self._agent_loop.mark_turn_failed(
            active_turn.conversation.actor_id,
            active_turn.turn.id,
            "model_invocation_failed",
            failure_message,
        )
        message = self._append_assistant_message(active_turn, failure_message)
        return CareerResponseResult(message, failed_turn, resolution)

    def _finish_with_advisory(
        self,
        active_turn,
        content: str,
        resolution: ModelResolution | None,
    ) -> CareerResponseResult:
        """把配置性提示作为可追溯助手消息写入，并正常收口已完成的安全处理工作。"""

        message = self._append_assistant_message(active_turn, content)
        succeeded_turn = self._agent_loop.mark_turn_succeeded(
            active_turn.conversation.actor_id,
            active_turn.turn.id,
        )
        return CareerResponseResult(message, succeeded_turn, resolution)

    def _append_assistant_message(self, active_turn, content: str) -> MessageRecord:
        """按当前部署的隐私开关处理模型输出后写入会话历史。"""

        normalized_content = content.strip()
        if not normalized_content:
            raise ValueError("助手回复不能为空")
        safe_content = self._redactor.redact(normalized_content)[
            : self._max_persisted_response_characters
        ]
        return self._agent_loop.repository.append_message(
            active_turn.conversation.id,
            MessageRole.ASSISTANT,
            safe_content,
            turn_id=active_turn.turn.id,
            is_redacted=self._redactor.enabled,
        )

    def _build_prompt(
        self,
        active_turn: ActiveAgentTurn,
        context: ModelTurnContext,
    ) -> list[ChatMessage]:
        """构造通用求职对话 Prompt，并按需附带本轮简历与职位材料。"""

        messages = [
            ChatMessage(
                role="system",
                content=(
                    "你是一名专业、自然、务实的中文求职助手。你既能正常聊天，也能在用户"
                    "提供简历、岗位描述或职位链接后做求职辅导。\n"
                    "回答规则：\n"
                    "1. 对问候、职业选择、简历写法、面试准备等普通提问，直接自然回答，"
                    "不要机械要求用户先上传简历。\n"
                    "2. 只有当用户要求具体的简历诊断或岗位匹配、而关键材料确实缺失时，才说明"
                    "还需要哪些信息；同时先给出当前可行的建议。\n"
                    "3. 如果本轮上下文明确写着已收到附件，绝不能说“没有收到简历”。"
                    "若 PDF 没有可提取文本，应如实说明它可能是扫描件，并建议重新上传可复制"
                    "文本的 PDF、图片简历，或直接粘贴内容。\n"
                    "4. 有简历与岗位材料时，给出具体判断、优势、风险和下一步；没有岗位材料时"
                    "不要虚构匹配度分数。\n"
                    "5. 不输出或推测姓名、手机号、邮箱、身份证号等个人身份信息。"
                ),
            ),
        ]

        history_messages = self._agent_loop.repository.list_messages(
            active_turn.conversation.actor_id,
            active_turn.conversation.id,
            limit=6,
        )
        for history_message in history_messages:
            if history_message.turn_id == active_turn.turn.id:
                continue
            if history_message.role.value not in {"user", "assistant"}:
                continue
            content = history_message.content_text.strip()
            if len(content) > 1_600:
                content = content[:1_600] + "…"
            messages.append(
                ChatMessage(
                    role=history_message.role.value,
                    content=content,
                ),
            )

        sections = [
            "本轮用户输入：\n"
            + (
                context.redacted_user_text
                or "用户本轮没有额外文字，只提交了附件或职位信息。"
            ),
        ]
        if context.redacted_material_text:
            sections.append(
                "本轮附件状态：已收到附件，并提取到以下可分析文本（已按当前隐私配置处理）：\n"
                + context.redacted_material_text,
            )
        if context.redacted_resume_outline:
            sections.append(
                "本轮简历结构化归纳（由确定性规则生成，存在待确认片段时应向用户核实，"
                "不得将其当作最终事实；已按当前隐私配置处理）：\n"
                + context.redacted_resume_outline,
            )
        if context.document_processing_notices:
            sections.append(
                "本轮文档解析状态：附件已经收到，但增强解析服务未完成。"
                "请如实说明当前限制，不能把它表述为用户未上传文件。\n"
                + "\n".join(context.document_processing_notices),
            )
        elif context.received_attachment_kinds:
            if context.pdf_without_extractable_text_count:
                sections.append(
                    "本轮附件状态：已收到 PDF 附件，但没有提取到可复制文本。"
                    "该文件可能是扫描件；不要说没有收到附件。",
                )
            else:
                sections.append(
                    "本轮附件状态：已收到附件，附件内容将通过本轮可用能力处理；"
                    "不要说没有收到附件。",
                )
        if context.redacted_job_text:
            sections.append(
                "职位页面可见文本（已按当前隐私配置处理）：\n"
                + context.redacted_job_text,
            )
        if context.redacted_interview_evidence:
            evidence_sections = []
            for evidence in context.redacted_interview_evidence:
                evidence_sections.append(
                    f"{evidence.citation}\n{evidence.content}",
                )
            sections.append(
                "面经库检索证据（仅作经验参考；引用某条经验时保留方括号来源标识，"
                "不要把它表述为岗位官方事实）：\n"
                + "\n\n---\n\n".join(evidence_sections),
            )

        user_content: str | list[dict[str, object]] = "\n\n".join(sections)
        if context.vision_images:
            user_content = [
                {"type": "text", "text": user_content},
                *[
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{image.media_type};base64,{image.data_base64}",
                        },
                    }
                    for image in context.vision_images
                ],
            ]

        messages.append(ChatMessage(role="user", content=user_content))
        return messages
