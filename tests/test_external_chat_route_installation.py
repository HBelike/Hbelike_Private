"""验证真实 Career 安装入口挂载全部聊天路由，不启动数据库或模型服务。"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from src.career_assistant.persistence.database import CareerDatabase
from src.career_assistant.web import router as career_router


def test_career_installation_registers_all_external_chat_routes_without_runtime(monkeypatch, tmp_path):
    def unexpected_runtime(*args, **kwargs):
        pytest.fail("路由安装和 OpenAPI 生成不得初始化数据库或 LLM 服务")

    monkeypatch.setattr(career_router, "get_career_read_services", unexpected_runtime)
    monkeypatch.setattr(career_router, "get_career_services", unexpected_runtime)
    monkeypatch.setattr(CareerDatabase, "__init__", unexpected_runtime)

    app = FastAPI()
    # 使用生产相同的安装入口，不能直接 include 子 router 掩盖漏挂载。
    career_router.install_career_assistant_api(
        app, tmp_path, skill_library_service=SimpleNamespace()
    )
    paths = app.openapi()["paths"]
    prefix = "/api/career/external-chat"
    expected = {
        "/sync": "post",
        "/accounts": "get",
        "/accounts/{account_id}/conversations": "get",
        "/conversations/{conversation_id}/messages": "get",
        "/accounts/{account_id}/execution": "post",
        "/accounts/{account_id}/execution/check": "post",
        "/accounts/{account_id}/execution/release": "post",
        "/accounts/{account_id}/pause": "post",
        "/accounts/{account_id}/resume": "post",
        "/conversations/{conversation_id}/operations": "post",
        "/accounts/{account_id}/operations": "get",
        "/operations/{client_operation_id}": "get",
        "/operations/{client_operation_id}/transition": "post",
        "/operations/{client_operation_id}/cancel": "post",
    }
    for path, method in expected.items():
        full_path = prefix + path
        assert full_path in paths, f"Career 安装入口漏挂载：{full_path}"
        assert method in paths[full_path], f"聊天路由缺少 {method.upper()} 方法：{full_path}"

    # 不进入 lifespan；登记清理钩子不等于此时就连接数据库或初始化模型。
    assert app.state.external_chat_cleanup_task is None
    assert app.state.career_assistant_read_services is None
    assert app.state.career_assistant_services is None
