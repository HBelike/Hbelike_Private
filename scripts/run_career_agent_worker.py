"""生产容器中的求职助手 Agent Worker 入口。"""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from fastapi import FastAPI, Request


PROJECT_ROOT = Path(__file__).resolve().parents[1]
# 直接执行 scripts 下的入口时，Python 只把 scripts 目录加入模块搜索路径。
# 显式加入项目根目录，保证本地隐藏进程和生产容器都能导入 src 包。
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.career_assistant.persistence import CareerTurnJobRepository
from src.career_assistant.settings import load_career_turn_worker_settings
from src.career_assistant.turn_worker import (
    CareerAgentTurnProcessor,
    CareerTurnWorker,
)
from src.career_assistant.web.router import (
    get_career_services,
    install_career_assistant_api,
)

async def run_worker() -> None:
    """复用求职助手服务工厂并启动不依赖 HTTP 连接的 Worker。"""

    app = FastAPI()
    install_career_assistant_api(app, PROJECT_ROOT)
    request = Request({"type": "http", "app": app})
    services = get_career_services(request)
    repository = CareerTurnJobRepository(services.database)
    worker = CareerTurnWorker(
        repository,
        CareerAgentTurnProcessor(
            services.agent_loop,
            services.intake_graph,
            services.response_runner,
        ),
        load_career_turn_worker_settings(),
    )
    try:
        await worker.run_forever()
    finally:
        services.close()


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(run_worker())
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
