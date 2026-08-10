"""求职助手模型档案的 PostgreSQL 仓储。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import RowMapping, text

from src.career_assistant.contracts import ModelCapability
from src.career_assistant.persistence.database import CareerDatabase


class ModelCostTier(StrEnum):
    """模型档案的费用属性；路由器依据它执行免费优先策略。"""

    FREE_QUOTA = "free_quota"
    LOCAL = "local"
    PAID = "paid"


@dataclass(frozen=True)
class ModelProfileDraft:
    """由模型设置页提交的无密钥模型档案内容。"""

    profile_key: str
    display_name: str
    provider_key: str
    model_id: str
    capabilities: frozenset[ModelCapability]
    cost_tier: ModelCostTier
    priority: int = 100
    enabled: bool = True
    api_base_url: str | None = None
    provider_website_url: str | None = None


@dataclass(frozen=True)
class ModelProfileRecord:
    """数据库中的模型档案视图，不包含 API Key。"""

    id: UUID
    organization_id: UUID
    profile_key: str
    display_name: str
    provider_key: str
    model_id: str
    capabilities: frozenset[ModelCapability]
    cost_tier: ModelCostTier
    priority: int
    enabled: bool
    api_base_url: str | None
    created_at: datetime
    updated_at: datetime
    provider_website_url: str | None = None


class CareerModelProfileRepository:
    """封装模型档案的增删查与输入校验。

    该仓储只操作 ``career_assistant.model_profiles``，不读取 .env，也不会把 Key 写入
    数据库。每次调用都独立使用事务，能安全服务于未来的多请求 Web API。
    """

    _PROFILE_KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

    def __init__(self, database: CareerDatabase) -> None:
        """保存独立 PostgreSQL 边界，不依赖旧 SQLite 管理器。"""

        self._database = database

    def upsert_profile(
        self,
        organization_id: UUID,
        draft: ModelProfileDraft,
        *,
        api_key: str | None = None,
    ) -> ModelProfileRecord:
        """按 profile_key 创建或更新档案，并可原子保存本机 API Key。

        早期仅使用环境变量的档案可继续不传 ``api_key``。如果传入密钥，档案记录和
        本机凭据会在同一个数据库事务内提交，任何一步失败都会回滚。
        """

        normalized_draft = self._normalize_draft(draft)
        normalized_api_key = api_key.strip() if api_key is not None else None
        if normalized_api_key is not None and not normalized_api_key:
            raise ValueError("API Key 不能为空")

        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.model_profiles (
                        id, organization_id, profile_key, display_name, provider_key,
                        model_id, api_base_url, provider_website_url, capability_codes,
                        cost_tier, enabled, priority
                    )
                    VALUES (
                        :id, :organization_id, :profile_key, :display_name, :provider_key,
                        :model_id, :api_base_url, :provider_website_url,
                        CAST(:capability_codes AS jsonb),
                        :cost_tier, :enabled, :priority
                    )
                    ON CONFLICT (organization_id, profile_key) DO UPDATE
                    SET display_name = EXCLUDED.display_name,
                        provider_key = EXCLUDED.provider_key,
                        model_id = EXCLUDED.model_id,
                        api_base_url = EXCLUDED.api_base_url,
                        provider_website_url = EXCLUDED.provider_website_url,
                        capability_codes = EXCLUDED.capability_codes,
                        cost_tier = EXCLUDED.cost_tier,
                        enabled = EXCLUDED.enabled,
                        priority = EXCLUDED.priority,
                        updated_at = NOW()
                    RETURNING id, organization_id, profile_key, display_name, provider_key,
                              model_id, api_base_url, provider_website_url, capability_codes,
                              cost_tier, enabled, priority, created_at, updated_at
                    """,
                ),
                {
                    "id": uuid4(),
                    "organization_id": organization_id,
                    "profile_key": normalized_draft.profile_key,
                    "display_name": normalized_draft.display_name,
                    "provider_key": normalized_draft.provider_key,
                    "model_id": normalized_draft.model_id,
                    "api_base_url": normalized_draft.api_base_url,
                    "provider_website_url": normalized_draft.provider_website_url,
                    "capability_codes": json.dumps(
                        sorted(capability.value for capability in normalized_draft.capabilities),
                    ),
                    "cost_tier": normalized_draft.cost_tier.value,
                    "enabled": normalized_draft.enabled,
                    "priority": normalized_draft.priority,
                },
            ).mappings().one()

            if normalized_api_key is not None:
                connection.execute(
                    text(
                        """
                        INSERT INTO career_assistant.model_profile_credentials (
                            profile_id, organization_id, plaintext_api_key, last_verified_at
                        )
                        VALUES (
                            :profile_id, :organization_id, :api_key,
                            NOW()
                        )
                        ON CONFLICT (profile_id) DO UPDATE
                        SET organization_id = EXCLUDED.organization_id,
                            plaintext_api_key = EXCLUDED.plaintext_api_key,
                            last_verified_at = NOW(),
                            updated_at = NOW()
                        """,
                    ),
                    {
                        "profile_id": row["id"],
                        "organization_id": organization_id,
                        "api_key": normalized_api_key,
                    },
                )

        return self._to_record(row)

    def has_stored_credential(
        self,
        organization_id: UUID,
        profile_id: UUID,
    ) -> bool:
        """只检查本机保存的 Key 是否存在，绝不向 Web 层返回 Key。"""

        with self._database.transaction() as connection:
            return connection.execute(
                text(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM career_assistant.model_profile_credentials
                        WHERE profile_id = :profile_id
                          AND organization_id = :organization_id
                          AND plaintext_api_key IS NOT NULL
                          AND BTRIM(plaintext_api_key) <> ''
                    )
                    """,
                ),
                {"profile_id": profile_id, "organization_id": organization_id},
            ).scalar_one()

    def read_stored_credential(
        self,
        organization_id: UUID,
        profile_id: UUID,
    ) -> str | None:
        """模型调用前读取本机 API Key；调用方不得日志记录或对外返回该值。"""

        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT plaintext_api_key AS api_key
                    FROM career_assistant.model_profile_credentials
                    WHERE profile_id = :profile_id
                      AND organization_id = :organization_id
                    """,
                ),
                {
                    "profile_id": profile_id,
                    "organization_id": organization_id,
                },
            ).mappings().one_or_none()

        return str(row["api_key"]) if row is not None else None

    def list_profiles(
        self,
        organization_id: UUID,
        *,
        include_disabled: bool,
    ) -> list[ModelProfileRecord]:
        """按配置优先级读取组织内模型档案。"""

        enabled_filter = "" if include_disabled else "AND enabled = TRUE"
        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    f"""
                    SELECT id, organization_id, profile_key, display_name, provider_key,
                           model_id, api_base_url, provider_website_url, capability_codes,
                           cost_tier, enabled, priority, created_at, updated_at
                    FROM career_assistant.model_profiles
                    WHERE organization_id = :organization_id
                    {enabled_filter}
                    ORDER BY priority ASC, profile_key ASC
                    """,
                ),
                {"organization_id": organization_id},
            ).mappings().all()

        return [self._to_record(row) for row in rows]

    def get_profile(
        self,
        organization_id: UUID,
        profile_id: UUID,
    ) -> ModelProfileRecord | None:
        """按组织范围读取单个模型档案，防止跨组织切换模型。"""

        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT id, organization_id, profile_key, display_name, provider_key,
                           model_id, api_base_url, provider_website_url, capability_codes,
                           cost_tier, enabled, priority, created_at, updated_at
                    FROM career_assistant.model_profiles
                    WHERE organization_id = :organization_id
                      AND id = :profile_id
                    """,
                ),
                {"organization_id": organization_id, "profile_id": profile_id},
            ).mappings().one_or_none()

        return self._to_record(row) if row is not None else None

    def delete_profile_permanently(
        self,
        organization_id: UUID,
        profile_id: UUID,
    ) -> bool:
        """永久删除未被历史 Turn 引用的模型档案。

        如果档案已经被历史 Turn 引用，数据库外键会拒绝删除，调用方应改为停用档案。
        """

        with self._database.transaction() as connection:
            result = connection.execute(
                text(
                    """
                    DELETE FROM career_assistant.model_profiles
                    WHERE id = :profile_id
                      AND organization_id = :organization_id
                    """,
                ),
                {"profile_id": profile_id, "organization_id": organization_id},
            )

        return result.rowcount == 1

    def _normalize_draft(self, draft: ModelProfileDraft) -> ModelProfileDraft:
        """在数据库写入前完成模型配置的边界检查。"""

        profile_key = draft.profile_key.strip().lower()
        if not self._PROFILE_KEY_PATTERN.fullmatch(profile_key):
            raise ValueError("profile_key 必须是 1 至 64 位小写字母、数字、短横线或下划线")

        display_name = self._normalize_text(draft.display_name, "display_name", 120)
        provider_key = self._normalize_provider_key(draft.provider_key)
        model_id = self._normalize_text(draft.model_id, "model_id", 200)
        if not draft.capabilities:
            raise ValueError("模型档案至少需要一种能力")
        if not isinstance(draft.enabled, bool):
            raise ValueError("enabled 必须是布尔值")
        if not 0 <= draft.priority <= 10_000:
            raise ValueError("priority 必须在 0 到 10000 之间")

        api_base_url = draft.api_base_url.strip() if draft.api_base_url else None
        if api_base_url and not api_base_url.startswith(("https://", "http://")):
            raise ValueError("api_base_url 必须是 http 或 https 地址")
        provider_website_url = (
            draft.provider_website_url.strip() if draft.provider_website_url else None
        )
        if provider_website_url and not provider_website_url.startswith(("https://", "http://")):
            raise ValueError("provider_website_url 必须是 http 或 https 地址")

        return ModelProfileDraft(
            profile_key=profile_key,
            display_name=display_name,
            provider_key=provider_key,
            model_id=model_id,
            capabilities=frozenset(draft.capabilities),
            cost_tier=draft.cost_tier,
            priority=draft.priority,
            enabled=draft.enabled,
            api_base_url=api_base_url,
            provider_website_url=provider_website_url,
        )

    @staticmethod
    def _normalize_text(value: str, field_name: str, maximum_length: int) -> str:
        """校验必要文本字段。"""

        normalized_value = value.strip()
        if not normalized_value:
            raise ValueError(f"{field_name} 不能为空")
        if len(normalized_value) > maximum_length:
            raise ValueError(f"{field_name} 不能超过 {maximum_length} 个字符")
        return normalized_value

    @staticmethod
    def _normalize_provider_key(value: str) -> str:
        """校验 Provider 标识格式。"""

        normalized_value = value.strip().lower()
        if not normalized_value or len(normalized_value) > 50:
            raise ValueError("provider_key 不能为空且不能超过 50 个字符")
        if not all(character.isalnum() or character in {"-", "_"} for character in normalized_value):
            raise ValueError("provider_key 只能包含小写字母、数字、短横线或下划线")
        return normalized_value

    @staticmethod
    def _to_record(row: RowMapping) -> ModelProfileRecord:
        """将 SQL 行转换为模型档案读取模型。"""

        return ModelProfileRecord(
            id=row["id"],
            organization_id=row["organization_id"],
            profile_key=row["profile_key"],
            display_name=row["display_name"],
            provider_key=row["provider_key"],
            model_id=row["model_id"],
            api_base_url=row["api_base_url"],
            provider_website_url=row["provider_website_url"],
            capabilities=frozenset(
                ModelCapability(capability) for capability in row["capability_codes"]
            ),
            cost_tier=ModelCostTier(row["cost_tier"]),
            enabled=row["enabled"],
            priority=row["priority"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
