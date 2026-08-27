from __future__ import annotations

import pytest

from src.repositories.weekly_ranking_repository import WeeklyRankingRecord
from src.tasks.summary_task import SummaryTask


def _ranking() -> WeeklyRankingRecord:
    return WeeklyRankingRecord(
        repository_id=1,
        rank=1,
        full_name="example/spec-kit",
        html_url="https://github.com/example/spec-kit",
        description="规格驱动开发工具",
        language="Python",
        current_stars=100,
        star_growth=10,
        growth_rate=0.1,
        score=1.0,
        reason="测试",
    )


def _project_analysis() -> str:
    return """
example/spec-kit 本周新增 10 stars，总 stars 为 100。它用规格约束实现，适合需要持续追踪需求的团队。

**技术特点**
需求规格、技术计划和任务清单分担不同职责，减少需求与实现脱节。

**机制拆解**
需求规格依次流向技术计划、任务清单和代码实现。

**工程启发**
把验收条件前置；规格质量仍需要人工确认，阅读时先看规格入口再看任务生成。
""".strip()


def _project_brief() -> dict[str, object]:
    return {
        "repository_full_name": "example/spec-kit",
        "summary_text": "example/spec-kit 用规格解决需求与实现脱节，通过计划和任务拆解约束编码；规格质量仍需人工确认。",
        "visual_brief": {
            "diagram_type": "linear_progression",
            "teaching_goal": "解释规格如何约束代码实现",
            "visual_thesis": "规格经过计划和任务拆解后进入实现",
            "nodes": [
                {"id": "spec", "label": "需求规格", "role": "事实输入"},
                {"id": "plan", "label": "技术计划", "role": "方案编排"},
                {"id": "tasks", "label": "任务清单", "role": "执行拆解"},
                {"id": "code", "label": "代码实现", "role": "交付结果"},
            ],
            "relationships": [
                {"from": "spec", "to": "plan", "label": "数据流"},
                {"from": "plan", "to": "tasks", "label": "同步调用"},
                {"from": "tasks", "to": "code", "label": "事件推送"},
            ],
            "reading_order": ["spec", "plan", "tasks", "code"],
            "chinese_labels": ["需求规格", "技术计划", "任务清单", "代码实现"],
        },
    }


def test_content_brief_uses_problem_mechanism_boundary_summary_and_real_nodes() -> None:
    task = object.__new__(SummaryTask)
    ranking = _ranking()

    briefs = task._build_content_briefs(
        rankings=[ranking],
        project_analyses={ranking.full_name: _project_analysis()},
        raw_project_briefs=[_project_brief()],
    )

    assert len(briefs) == 1
    assert briefs[0]["summary_text"].startswith("该项目用规格解决需求与实现脱节")
    assert "人工确认" in briefs[0]["summary_text"]
    assert briefs[0]["prompt"] == "规格经过计划和任务拆解后进入实现"
    assert briefs[0]["prompt_stage"] == "content_brief_v2"
    assert [node["label"] for node in briefs[0]["visual_brief"]["nodes"]] == [
        "需求规格",
        "技术计划",
        "任务清单",
        "代码实现",
    ]
    assert any(
        relation["label"] == "数据流"
        for relation in briefs[0]["visual_brief"]["relationships"]
    )


def test_content_brief_rejects_generic_placeholder_nodes() -> None:
    task = object.__new__(SummaryTask)
    ranking = _ranking()
    generic_brief = _project_brief()
    generic_brief["visual_brief"] = {
        **generic_brief["visual_brief"],
        "nodes": [
            {"id": "input", "label": "输入", "role": "起点"},
            {"id": "core", "label": "核心", "role": "处理"},
            {"id": "output", "label": "输出", "role": "结果"},
        ],
    }

    with pytest.raises(ValueError, match="通用占位标签"):
        task._build_content_briefs(
            rankings=[ranking],
            project_analyses={ranking.full_name: _project_analysis()},
            raw_project_briefs=[generic_brief],
        )
