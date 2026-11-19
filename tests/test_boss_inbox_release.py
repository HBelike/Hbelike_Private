"""只在本机隔离库验证真实升级链及旧聊天数据兼容。"""

from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError


ROOT = Path(__file__).resolve().parents[1]


def test_full_upgrade_chain_keeps_existing_chat_and_adds_inbox(monkeypatch):
    value = os.getenv("EXTERNAL_CHAT_TEST_DATABASE_URL") or os.getenv("CAREER_DATABASE_URL") or dotenv_values(ROOT / ".env.career-assistant").get("CAREER_DATABASE_URL")
    if not value:
        pytest.skip("真实升级链验证需要本地 PostgreSQL")
    url = make_url(value).set(drivername="postgresql+psycopg")
    if url.host not in {"127.0.0.1", "localhost", "::1"}:
        pytest.skip("升级链测试只允许连接本机 PostgreSQL")
    database_name = "codex_inbox_release_" + uuid4().hex
    admin = create_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT", connect_args={"connect_timeout": 3})
    created = False
    engine = None
    try:
        try:
            with admin.connect() as connection:
                connection.execute(text(f'CREATE DATABASE "{database_name}"'))
            created = True
        except SQLAlchemyError:
            pytest.skip("本地 PostgreSQL 未就绪或无权限创建隔离测试库")
        isolated_url = url.set(database=database_name)
        monkeypatch.setenv("CAREER_DATABASE_URL", isolated_url.render_as_string(hide_password=False))
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "migrations"))
        command.upgrade(config, "20260901_33")
        engine = create_engine(isolated_url)
        org, actor, conversation, message = (uuid4() for _ in range(4))
        with engine.begin() as connection:
            connection.execute(text("INSERT INTO career_assistant.organizations (id,slug,display_name) VALUES (:id,:slug,'升级验收')"), {"id": org, "slug": str(org)})
            connection.execute(text("INSERT INTO career_assistant.actors (id,organization_id,display_name,actor_type) VALUES (:id,:org,'升级验收用户','user')"), {"id": actor, "org": org})
            connection.execute(text("INSERT INTO career_assistant.conversations (id,organization_id,actor_id,title) VALUES (:id,:org,:actor,'原有会话')"), {"id": conversation, "org": org, "actor": actor})
            connection.execute(text("INSERT INTO career_assistant.messages (id,conversation_id,role,content_text) VALUES (:id,:conversation,'user','升级前已有消息')"), {"id": message, "conversation": conversation})
        command.upgrade(config, "20261109_35")
        # 重复执行同一目标应为空操作，不能重复建表或改动原有聊天。
        command.upgrade(config, "20261109_35")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == "20261109_35"
            assert connection.scalar(text("SELECT content_text FROM career_assistant.messages WHERE id=:id"), {"id": message}) == "升级前已有消息"
            assert connection.scalar(text("SELECT ui_payload FROM career_assistant.messages WHERE id=:id"), {"id": message}) is None
            tables = connection.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema='career_assistant' AND table_name LIKE 'external_chat_%'" )).scalars().all()
            assert set(tables) == {"external_chat_accounts", "external_chat_conversations", "external_chat_messages"}
        # 还要覆盖当前候选包的 head，防止它与本功能迁移形成分叉或冲突。
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT content_text FROM career_assistant.messages WHERE id=:id"), {"id": message}) == "升级前已有消息"
    finally:
        if engine is not None:
            engine.dispose()
        if created:
            # 只删除此测试刚生成的本机隔离库，绝不操作配置中的业务库。
            with admin.connect() as connection:
                connection.execute(text(f'DROP DATABASE "{database_name}" WITH (FORCE)'))
        admin.dispose()
