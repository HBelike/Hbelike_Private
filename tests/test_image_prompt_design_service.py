from __future__ import annotations

from pathlib import Path

from src.config.config_manager import ConfigManager
from src.services.image_prompt_design_service import ImagePromptDesignService
from src.services.media_creative_brief_service import MediaCreativeBriefService


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_seedream_prompt_uses_compact_ppt_teaching_contract() -> None:
    config = ConfigManager(PROJECT_ROOT).load()
    service = ImagePromptDesignService(config)
    prompt = service.build_project_architecture_prompt(
        repository_full_name="example/spec-kit",
        focus_prompt="规格驱动开发把需求逐步编译为计划和任务。",
        project_summary_text="规格先成为可执行输入，再经过计划和任务拆解进入实现。",
        visual_brief={
            "diagram_type": "linear_progression",
            "teaching_goal": "解释规格如何成为可执行开发流程",
            "visual_thesis": "规格经过计划与任务拆解后约束实现",
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
        project_index=1,
    )

    assert len(prompt) <= 300
    assert "白底" in prompt
    assert "商务技术PPT质感" in prompt
    assert "矩形模块" in prompt
    assert "正交折线箭头" in prompt
    assert "严格禁用曲线" in prompt
    assert "需求规格" in prompt
    assert "同步调用" in prompt
    assert "只显示上述模块名和连线标签" in prompt
    assert "禁标题、正文、页码" in prompt
    assert "example/spec-kit" not in prompt


def test_visual_brief_limits_nodes_and_normalizes_relationship_labels() -> None:
    brief = MediaCreativeBriefService().normalize_visual_brief(
        raw_brief={
            "diagram_type": "hub_spoke",
            "nodes": [
                {"id": f"node_{index}", "label": f"模块{index}", "role": "组件"}
                for index in range(1, 7)
            ],
            "relationships": [
                {"from": "node_1", "to": "node_2", "label": "调用"},
                {"from": "node_2", "to": "node_3", "label": "异步"},
                {"from": "node_3", "to": "node_4", "label": "自定义关系"},
            ],
        },
        repository_full_name="example/project",
        fallback_text="模块协作关系",
        project_index=1,
    )

    assert len(brief["nodes"]) == 4
    assert [item["label"] for item in brief["relationships"]] == [
        "同步调用",
        "异步调用",
        "数据流",
    ]
    assert len(brief["chinese_labels"]) == 4
