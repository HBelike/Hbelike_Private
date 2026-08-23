"""持久化 Turn 队列的数据结构与仓储测试。"""

from __future__ import annotations

import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_PATH = (
    PROJECT_ROOT
    / "migrations"
    / "versions"
    / "20260823_17_career_turn_worker_queue.py"
)


class CareerTurnQueueMigrationTests(unittest.TestCase):
    def test_queue_migration_contains_required_tables_columns_and_indexes(self) -> None:
        source = MIGRATION_PATH.read_text(encoding="utf-8")

        for token in (
            'revision = "20260823_17"',
            'down_revision = "20260821_16"',
            "queue_sequence",
            "available_at",
            "attempt_count",
            "lease_owner",
            "lease_expires_at",
            "heartbeat_at",
            "cancel_requested_at",
            "agent_turn_payloads",
            "attachment_payloads_json",
            "request_metadata_json",
            "agent_turn_events",
            "agent_execution_slots",
            "idx_career_agent_turns_queue_claim",
            "idx_career_agent_turns_conversation_queue",
        ):
            self.assertIn(token, source)

    def test_queue_migration_has_a_complete_downgrade(self) -> None:
        source = MIGRATION_PATH.read_text(encoding="utf-8")

        self.assertIn("def downgrade() -> None:", source)
        self.assertIn("DROP TABLE career_assistant.agent_execution_slots", source)
        self.assertIn("DROP TABLE career_assistant.agent_turn_events", source)
        self.assertIn("DROP TABLE career_assistant.agent_turn_payloads", source)
        self.assertIn("DROP COLUMN queue_sequence", source)


if __name__ == "__main__":
    unittest.main()
