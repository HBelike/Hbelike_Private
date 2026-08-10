from __future__ import annotations

import os
from pathlib import Path

import uvicorn

from src.web.api import create_app


app = create_app()


if __name__ == "__main__":
    # 本地 Web API 的唯一开发入口。Vite 默认代理到此端口。
    project_root = Path(__file__).resolve().parent
    port = int(os.environ.get("PREVIEW_SERVER_PORT", "18080"))
    reload_enabled = os.environ.get("PREVIEW_SERVER_RELOAD", "true").lower() in {
        "1",
        "true",
        "yes",
    }

    # Windows、网络盘与桌面应用工作区里，原生文件事件有时不会传给 WatchFiles。
    # 仅开发环境启用轮询，保证保存 Python/YAML 后能够稳定重载。
    if reload_enabled:
        os.environ.setdefault("WATCHFILES_FORCE_POLLING", "true")
        os.environ.setdefault("WATCHFILES_POLL_DELAY_MS", "300")

    uvicorn.run(
        "preview_server:app",
        host="127.0.0.1",
        port=port,
        reload=reload_enabled,
        reload_dirs=[str(project_root / "src"), str(project_root / "config")]
        if reload_enabled
        else None,
        reload_includes=["*.py", "*.yaml"] if reload_enabled else None,
    )
