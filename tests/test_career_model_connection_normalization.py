"""模型连接路由规范化测试。"""

from src.career_assistant.contracts import ModelCapability
from src.career_assistant.persistence.model_profile_repository import ModelCostTier
from src.career_assistant.web.router import (
    ModelConnectionRequest,
    _normalized_model_connection_target,
)


def test_kimi_custom_request_is_normalized_before_verification() -> None:
    request = ModelConnectionRequest(
        display_name="Kimi K3",
        provider_key="custom",
        model_id="kimi K3",
        api_key="test-key",
        capabilities={ModelCapability.TEXT},
        cost_tier=ModelCostTier.PAID,
        api_base_url="https://api.moonshot.cn/v1/",
    )

    target = _normalized_model_connection_target(request)

    assert target.provider_key == "kimi"
    assert target.model_id == "kimi-k3"
    assert target.api_base_url == "https://api.moonshot.cn/v1"
