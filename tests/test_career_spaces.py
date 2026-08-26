"""职业空间迁移和会话绑定测试。"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations" / "versions" / "20260826_24_career_long_term_memory.py"


def test_migration_creates_scoped_spaces_memories_jobs_and_usage_trace() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for token in (
        'revision = "20260826_24"',
        'down_revision = "20260826_23"',
        "career_spaces",
        "career_memory_items",
        "career_memory_jobs",
        "turn_memory_usages",
        "career_space_id",
        "uq_career_spaces_actor_default",
        "uq_career_memory_jobs_turn",
        "uq_career_memory_jobs_resume",
        "memory_job_id",
        "chk_model_usage_owner",
    ):
        assert token in source


def test_migration_only_allows_six_approved_memory_types() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    for memory_type in (
        "job_intention",
        "work_experience",
        "education",
        "award",
        "publication",
        "personal_advantage",
    ):
        assert memory_type in source
    assert "hobby" not in source
    assert "relationship" not in source


def test_migration_backfills_default_space_before_not_null() -> None:
    source = MIGRATION.read_text(encoding="utf-8")
    insert_at = source.index("INSERT INTO career_assistant.career_spaces")
    update_at = source.index("UPDATE career_assistant.conversations")
    not_null_at = source.index("ALTER COLUMN career_space_id SET NOT NULL")
    assert insert_at < update_at < not_null_at
