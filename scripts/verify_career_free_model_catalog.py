"""离线验证求职助手免费模型目录的关键接入信息。"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import urlparse


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.career_assistant.free_model_catalog import (
    FREE_MODEL_PROVIDERS,
    build_free_model_catalog_payload,
)


def _assert_https_url(value: str, field_name: str) -> None:
    parsed = urlparse(value)
    assert parsed.scheme == "https", f"{field_name} 必须使用 HTTPS：{value}"
    assert parsed.netloc, f"{field_name} 缺少域名：{value}"


def main() -> None:
    """阻止错误模型 ID、缺失官方链接和错误能力标记重新进入目录。"""

    providers = {provider.provider_key: provider for provider in FREE_MODEL_PROVIDERS}
    assert len(providers) == len(FREE_MODEL_PROVIDERS), "provider_key 不得重复"
    assert set(providers) == {
        "gemini",
        "qwen",
        "openrouter",
        "modelscope",
        "siliconflow",
        "nvidia",
    }

    for provider in FREE_MODEL_PROVIDERS:
        for field_name in (
            "api_base_url",
            "website_url",
            "setup_url",
            "documentation_url",
            "pricing_url",
        ):
            _assert_https_url(getattr(provider, field_name), f"{provider.provider_key}.{field_name}")

        model_ids = [template.model_id for template in provider.templates]
        assert model_ids, f"{provider.provider_key} 至少需要一个候选模型"
        assert len(model_ids) == len(set(model_ids)), f"{provider.provider_key} 的 model_id 不得重复"

    gemini_models = {item.model_id: item for item in providers["gemini"].templates}
    assert gemini_models["gemini-3.5-flash-lite"].supports_vision is True

    siliconflow_ids = {item.model_id for item in providers["siliconflow"].templates}
    assert "Qwen/Qwen3-8B" in siliconflow_ids
    assert "Qwen/Qwen-3-8B" not in siliconflow_ids

    nvidia_ids = {item.model_id for item in providers["nvidia"].templates}
    assert "meta/llama-3.3-70b-instruct" in nvidia_ids
    assert "stockmark/stockmark-2-100b-instruct" not in nvidia_ids

    payload = build_free_model_catalog_payload([])
    assert len(payload) == len(FREE_MODEL_PROVIDERS)
    assert all(item["pricing_url"] for item in payload)
    assert all(item["platform_ready"] is False for item in payload)
    assert all(item["visitor_ready"] is False for item in payload)

    print("career_free_model_catalog_ok")


if __name__ == "__main__":
    main()
