from __future__ import annotations

from unittest.mock import patch

from src.career_assistant.settings import load_model_gateway_settings


def test_platform_shared_deepseek_default_is_enabled_without_user_configuration() -> None:
    with patch.dict("os.environ", {}, clear=True):
        settings = load_model_gateway_settings()

    assert settings.provider_credential_env["deepseek"] == "DEEPSEEK_API_KEY"
    assert settings.provider_credential_env["kimi"] == "MOONSHOT_API_KEY"
    assert settings.allow_paid_profiles is True
    assert settings.default_provider_key == "deepseek"
    assert settings.default_model_id == "deepseek-v4-pro"


def test_administrator_can_explicitly_disable_paid_connections() -> None:
    with patch.dict("os.environ", {"CAREER_ALLOW_PAID_PROFILES": "false"}, clear=True):
        assert load_model_gateway_settings().allow_paid_profiles is False


def test_local_environment_can_explicitly_enable_paid_deepseek_profile() -> None:
    with patch.dict("os.environ", {"CAREER_ALLOW_PAID_PROFILES": "true"}, clear=True):
        settings = load_model_gateway_settings()

    assert settings.allow_paid_profiles is True
