"""平台访问模块的数据契约。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from uuid import UUID


class PlatformRole(str, Enum):
    """平台权限等级，按查看、执行、管理逐级递增。"""

    VIEWER = "viewer"
    OPERATOR = "operator"
    ADMIN = "admin"

    def allows(self, required: "PlatformRole") -> bool:
        """判断当前角色是否包含目标操作权限。"""

        ranks = {
            PlatformRole.VIEWER: 1,
            PlatformRole.OPERATOR: 2,
            PlatformRole.ADMIN: 3,
        }
        return ranks[self] >= ranks[required]


@dataclass(frozen=True)
class PlatformUser:
    """已认证的最小用户视图，不携带密码散列。"""

    id: UUID
    organization_id: UUID
    username: str
    display_name: str
    email: str | None
    email_verified_at: datetime | None
    role: PlatformRole
    is_active: bool
    created_at: datetime


@dataclass(frozen=True)
class SessionResolution:
    """服务端会话解析结果。"""

    user: PlatformUser
    session_id: UUID
    expires_at: datetime
    absolute_expires_at: datetime
