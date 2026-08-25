"""平台顶级路由模块的默认目录与可见性规则。"""

from __future__ import annotations

from typing import Any

from src.platform_access.contracts import PlatformRole


ROUTE_MODULE_DEFINITIONS: tuple[dict[str, Any], ...] = (
    {
        "key": "career_assistant",
        "label": "求职助手",
        "path": "/career",
        "description": "简历匹配、岗位分析与职业咨询",
        "admin_only": False,
        "locked": False,
    },
    {
        "key": "workbench",
        "label": "工作台",
        "path": "/review",
        "description": "公众号内容审核与媒体工作流",
        "admin_only": False,
        "locked": False,
    },
    {
        "key": "resume_assistant",
        "label": "简历助手",
        "path": "/resume-assistant",
        "description": "面向目标岗位生成可审核的简历版本",
        "admin_only": False,
        "locked": False,
    },
    {
        "key": "interview_library",
        "label": "面经库",
        "path": "/interviews",
        "description": "结构化面经沉淀与检索",
        "admin_only": False,
        "locked": False,
    },
    {
        "key": "job_library",
        "label": "职位库",
        "path": "/interviews/jobs",
        "description": "通过浏览器助手查找当前在招职位",
        "admin_only": False,
        "locked": False,
    },
    {
        "key": "skill_library",
        "label": "技能库",
        "path": "/skills",
        "description": "本地 Skill 查找、查看与维护",
        "admin_only": False,
        "locked": False,
    },
    {
        "key": "evaluation_center",
        "label": "评测中心",
        "path": "/evaluations",
        "description": "真实数据实验、指标对比与发布门槛",
        "admin_only": True,
        "locked": False,
    },
    {
        "key": "langsmith",
        "label": "LangSmith",
        "path": "/observability",
        "description": "模型调用链路与运行观测",
        "admin_only": True,
        "locked": False,
    },
    {
        "key": "admin_console",
        "label": "管理台",
        "path": "/admin/modules",
        "description": "平台路由、运行参数与内容工作流配置",
        "admin_only": True,
        "locked": True,
    },
)

DEFAULT_ROUTE_MODULE_SETTINGS: dict[str, bool] = {
    definition["key"]: True for definition in ROUTE_MODULE_DEFINITIONS
}


def normalize_route_module_settings(value: dict[str, object] | None) -> dict[str, bool]:
    """将管理员输入合并到完整模块目录，并拒绝未知键与非布尔值。"""

    source = value or {}
    unknown_keys = sorted(set(source) - set(DEFAULT_ROUTE_MODULE_SETTINGS))
    if unknown_keys:
        raise ValueError(f"存在未知路由模块：{', '.join(unknown_keys)}")

    normalized = dict(DEFAULT_ROUTE_MODULE_SETTINGS)
    for key, enabled in source.items():
        if not isinstance(enabled, bool):
            raise ValueError(f"路由模块 {key} 的启用状态必须是布尔值")
        normalized[key] = enabled

    # 管理台是管理员恢复其他模块配置的唯一入口，不能被关闭。
    normalized["admin_console"] = True
    return normalized


def route_modules_for_ui(value: dict[str, object] | None, role: PlatformRole) -> list[dict[str, object]]:
    """返回 UI 可直接渲染的有序模块列表；管理员不受展示开关限制。"""

    settings = normalize_route_module_settings(value)
    is_admin = role.allows(PlatformRole.ADMIN)
    return [
        {
            **definition,
            "enabled": settings[str(definition["key"])],
            "accessible": is_admin or (
                settings[str(definition["key"])] and not definition["admin_only"]
            ),
        }
        for definition in ROUTE_MODULE_DEFINITIONS
    ]
