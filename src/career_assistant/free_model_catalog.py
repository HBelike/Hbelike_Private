"""求职助手的官方免费模型目录。

目录只记录公开、非敏感的服务商接入信息。真正的 API Key 始终由模型连接仓储或
部署环境保存；目录本身不会制造匿名调用，也不会把平台 Key 返回给浏览器。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from src.career_assistant.model_gateway import (
    ModelProfileAvailability,
    ModelReadiness,
)
from src.career_assistant.persistence.model_profile_repository import ModelCostTier


@dataclass(frozen=True)
class FreeModelTemplate:
    """一个可直接填入连接表单的官方模型候选项。

    ``model_id`` 是服务商当前文档中公开的调用标识；服务商可能调整可用目录，
    管理员仍应以其控制台的实时模型列表为准。
    """

    model_id: str
    display_name: str
    supports_vision: bool = False


@dataclass(frozen=True)
class FreeModelProvider:
    """可接入平台的免费额度服务商定义。

    所有云端服务商都要求平台管理员拥有自己的 API Key。管理员保存并验证连接后，
    平台访客可复用该连接，不需要在浏览器侧配置或看到 Key。
    """

    provider_key: str
    display_name: str
    api_base_url: str
    website_url: str
    setup_url: str
    documentation_url: str
    free_label: str
    free_description: str
    templates: tuple[FreeModelTemplate, ...]


FREE_MODEL_PROVIDERS: tuple[FreeModelProvider, ...] = (
    FreeModelProvider(
        provider_key="gemini",
        display_name="Google Gemini",
        api_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        website_url="https://aistudio.google.com",
        setup_url="https://aistudio.google.com/apikey",
        documentation_url="https://ai.google.dev/gemini-api/docs/pricing",
        free_label="免费层",
        free_description="Gemini Developer API 的 Free Tier 提供部分模型的免费输入与输出额度。",
        templates=(
            FreeModelTemplate("gemini-3.5-flash-lite", "Gemini 3.5 Flash-Lite"),
            FreeModelTemplate("gemini-3.5-flash", "Gemini 3.5 Flash", supports_vision=True),
            FreeModelTemplate("gemini-3.6-flash", "Gemini 3.6 Flash", supports_vision=True),
        ),
    ),
    FreeModelProvider(
        provider_key="qwen",
        display_name="阿里云百炼 Qwen",
        api_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        website_url="https://bailian.console.aliyun.com",
        setup_url="https://bailian.console.aliyun.com",
        documentation_url="https://help.aliyun.com/en/model-studio/text-generation-model/",
        free_label="新用户免费额度",
        free_description=(
            "百炼为新激活用户的部分模型提供限时免费额度；额度、区域与模型准入以控制台为准。"
            "平台保存并验证 DASHSCOPE_API_KEY 后，访客可直接使用。"
        ),
        templates=(
            # 此通用 Vision-Language 模型可作为管理员另行保存的会话模型；
            # 平台自动图片解析由独立 CloudVision 服务调用，不依赖此目录的选择。
            FreeModelTemplate(
                "qwen3.6-flash",
                "Qwen 3.6 Flash（通用图文理解）",
                supports_vision=True,
            ),
        ),
    ),
    FreeModelProvider(
        provider_key="openrouter",
        display_name="OpenRouter",
        api_base_url="https://openrouter.ai/api/v1",
        website_url="https://openrouter.ai",
        setup_url="https://openrouter.ai/keys",
        documentation_url="https://openrouter.ai/docs/guides/routing/model-variants/free",
        free_label="免费模型路由",
        free_description="使用 openrouter/free 路由到当时可用的免费模型，具体模型与限额由平台动态调整。",
        templates=(
            FreeModelTemplate("openrouter/free", "OpenRouter Free 自动路由"),
        ),
    ),
    FreeModelProvider(
        provider_key="modelscope",
        display_name="ModelScope 魔搭",
        api_base_url="https://api-inference.modelscope.cn/v1",
        website_url="https://www.modelscope.cn",
        setup_url="https://modelscope.cn/my/myaccesstoken",
        documentation_url="https://www.modelscope.cn/learn/434591",
        free_label="免费日额度",
        free_description="API Inference 为选定开源模型提供免费层，实际可调用目录与每日额度以控制台为准。",
        templates=(
            FreeModelTemplate("Qwen/Qwen2.5-7B-Instruct", "Qwen 2.5 7B Instruct"),
            FreeModelTemplate("Qwen/Qwen2.5-Coder-32B-Instruct", "Qwen 2.5 Coder 32B"),
        ),
    ),
    FreeModelProvider(
        provider_key="siliconflow",
        display_name="硅基流动 SiliconFlow",
        api_base_url="https://api.siliconflow.cn/v1",
        website_url="https://cloud.siliconflow.cn",
        setup_url="https://cloud.siliconflow.cn/account/ak",
        documentation_url="https://docs.siliconflow.cn/cn/userguide/rate-limits/rate-limit-and-upgradation",
        free_label="认证后免费模型",
        free_description="仅列出硅基流动标记为免费的模型；同名 Pro 前缀模型属于收费版本。",
        templates=(
            FreeModelTemplate("Qwen/Qwen2.5-7B-Instruct", "Qwen 2.5 7B Instruct"),
            FreeModelTemplate("Qwen/Qwen-3-8B", "Qwen 3 8B"),
            FreeModelTemplate("THUDM/GLM-4-9B-0414", "GLM 4 9B"),
        ),
    ),
    FreeModelProvider(
        provider_key="nvidia",
        display_name="NVIDIA NIM",
        api_base_url="https://integrate.api.nvidia.com/v1",
        website_url="https://build.nvidia.com",
        setup_url="https://build.nvidia.com",
        documentation_url="https://build.nvidia.com/stockmark/stockmark-2-100b-instruct/deploy",
        free_label="免费原型端点",
        free_description="NVIDIA Build 为部分模型提供免费原型推理端点；适合试验，不应假定为无限生产额度。",
        templates=(
            FreeModelTemplate("stockmark/stockmark-2-100b-instruct", "Stockmark 2 100B Instruct"),
        ),
    ),
)


def build_free_model_catalog_payload(
    availabilities: Iterable[ModelProfileAvailability],
) -> list[dict[str, object]]:
    """生成给 WebUI 的目录，并标记哪些免费连接已可供平台访客使用。

    只有已启用、已验证可调用、且由管理员显式标记为 ``free_quota`` 的连接才会被
    视为访客可用。这避免将“存在于免费目录但尚未配置 Key”的服务商误报为可调用。
    """

    available_by_provider: dict[str, list[ModelProfileAvailability]] = {}
    for item in availabilities:
        if (
            item.readiness is ModelReadiness.READY
            and item.profile.cost_tier is ModelCostTier.FREE_QUOTA
        ):
            available_by_provider.setdefault(item.profile.provider_key, []).append(item)

    payload: list[dict[str, object]] = []
    for provider in FREE_MODEL_PROVIDERS:
        ready_connections = available_by_provider.get(provider.provider_key, [])
        payload.append(
            {
                "provider_key": provider.provider_key,
                "display_name": provider.display_name,
                "api_base_url": provider.api_base_url,
                "website_url": provider.website_url,
                "setup_url": provider.setup_url,
                "documentation_url": provider.documentation_url,
                "free_label": provider.free_label,
                "free_description": provider.free_description,
                "platform_ready": bool(ready_connections),
                "visitor_ready": bool(ready_connections),
                "availability_label": (
                    "平台已启用，访客无需配置 Key"
                    if ready_connections
                    else "需管理员申请并保存 API Key"
                ),
                "configured_profiles": [
                    {
                        "id": str(item.profile.id),
                        "display_name": item.profile.display_name,
                        "model_id": item.profile.model_id,
                    }
                    for item in ready_connections
                ],
                "models": [
                    {
                        "model_id": template.model_id,
                        "display_name": template.display_name,
                        "supports_vision": template.supports_vision,
                    }
                    for template in provider.templates
                ],
            },
        )
    return payload
