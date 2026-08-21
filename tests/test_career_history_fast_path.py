"""求职助手历史列表轻量初始化边界测试。"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Lock
from unittest.mock import patch

from fastapi import FastAPI, Request

from src.career_assistant.web import router as career_router


class FakeDatabase:
    """记录测试连接边界，不创建真实 PostgreSQL Engine。"""

    def __init__(self, database_url: str) -> None:
        self.database_url = database_url


class FakeConversationRepository:
    """用于确认快速路径只构造会话仓储。"""

    def __init__(self, database: FakeDatabase) -> None:
        self.database = database


class FakeRepository:
    def __init__(self, database: FakeDatabase) -> None:
        self.database = database


class FakeModelGateway:
    def __init__(self, repository: FakeRepository, _settings) -> None:
        self.repository = repository


class CareerHistoryFastPathTests(unittest.TestCase):
    def test_history_repository_does_not_initialize_full_career_services(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            app = FastAPI()
            app.state.career_assistant_project_root = Path(temporary_directory)
            app.state.career_assistant_read_services = None
            app.state.career_assistant_repository_lock = Lock()
            app.state.career_assistant_services = None

            with (
                patch.dict(
                    os.environ,
                    {"CAREER_DATABASE_URL": "postgresql://career:test@localhost/career"},
                ),
                patch.object(career_router, "_load_career_environment", lambda _root: None),
                patch.object(career_router, "CareerDatabase", FakeDatabase),
                patch.object(career_router, "CareerContextRepository", FakeRepository),
                patch.object(career_router, "CareerModelProfileRepository", FakeRepository),
                patch.object(career_router, "ModelGateway", FakeModelGateway),
                patch.object(career_router, "load_model_gateway_settings", lambda _path: object()),
                patch.object(
                    career_router,
                    "CareerConversationRepository",
                    FakeConversationRepository,
                ),
            ):
                request = Request({"type": "http", "app": app})
                first = career_router.get_career_read_services(request)
                second = career_router.get_career_read_services(request)

        self.assertIs(first, second)
        self.assertIs(first, app.state.career_assistant_read_services)
        self.assertIsNone(app.state.career_assistant_services)


if __name__ == "__main__":
    unittest.main()
