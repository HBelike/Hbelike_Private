"""验证模型连接的加密凭据持久化与动态 Provider 路由。"""

from __future__ import annotations

import os
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.career_assistant.contracts import (
    ModelCapability,
    ModelSelectionMode,
    ModelSelectionRequest,
)
from src.career_assistant.model_gateway import ModelGateway, ModelReadiness
from src.career_assistant.persistence import (
    CareerDatabase,
    CareerModelProfileRepository,
    ModelCostTier,
    ModelProfileDraft,
)
from src.career_assistant.persistence.conversation_repository import DEFAULT_ORGANIZATION_ID
from src.career_assistant.settings import load_model_gateway_settings


def main() -> None:
    """写入临时本机凭据、读取验证后删除，确保不遗留测试模型。"""

    database_url = os.getenv("CAREER_DATABASE_URL", "").strip()
    if not database_url:
        raise RuntimeError("请通过 CAREER_DATABASE_URL 提供求职助手 PostgreSQL 连接串")

    database = CareerDatabase(database_url)
    repository = CareerModelProfileRepository(database)
    profile_id = None
    api_key = "verification-only-api-key"
    try:
        profile = repository.upsert_profile(
            DEFAULT_ORGANIZATION_ID,
            ModelProfileDraft(
                profile_key="verification-custom-connection",
                display_name="验证自定义连接",
                provider_key="custom",
                model_id="verification-model",
                api_base_url="https://mock-provider.example/v1",
                capabilities=frozenset({ModelCapability.TEXT}),
                cost_tier=ModelCostTier.FREE_QUOTA,
            ),
            api_key=api_key,
        )
        profile_id = profile.id
        assert repository.has_stored_credential(DEFAULT_ORGANIZATION_ID, profile.id)
        assert (
            repository.read_stored_credential(
                DEFAULT_ORGANIZATION_ID,
                profile.id,
            )
            == api_key
        )

        gateway = ModelGateway(
            repository,
            load_model_gateway_settings(),
            environment={},
        )
        resolution = gateway.resolve(
            DEFAULT_ORGANIZATION_ID,
            ModelSelectionRequest(
                mode=ModelSelectionMode.SPECIFIC_PROFILE,
                profile_id=profile.id,
                required_capabilities=frozenset({ModelCapability.TEXT}),
            ),
        )
        assert resolution.readiness is ModelReadiness.READY
        assert resolution.credential == api_key
        print("career_model_connection_credentials_real_database_ok")
    finally:
        if profile_id is not None:
            deleted = repository.delete_profile_permanently(
                DEFAULT_ORGANIZATION_ID,
                profile_id,
            )
            if not deleted:
                raise RuntimeError("模型连接凭据验证数据清理失败，请检查数据库")
        database.close()


if __name__ == "__main__":
    main()
