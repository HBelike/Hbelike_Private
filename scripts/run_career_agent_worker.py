"""生产容器中的求职助手 Agent Worker 入口。"""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI, Request

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


PROJECT_ROOT = Path(__file__).resolve().parents[1]


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
