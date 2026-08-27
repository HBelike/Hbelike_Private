from __future__ import annotations

import re
from typing import Any

from src.services.article_visual_spec import (
    ARTICLE_VISUAL_SPEC_VERSION,
    ArticleVisualSpecError,
    ValidatedArticleVisualSpec,
)
from src.services.article_visual_spec_validator import ArticleVisualSpecValidator


class ArticleVisualPlanningService:
    """应用图型选择合同，并为历史文章生成保守的总结卡规格。"""

    _legacy_sections = ("技术特点", "机制拆解", "工程启发")
    _diagram_types = {
        "summary_card": "structural_breakdown",
        "flow": "linear_progression",
        "architecture": "layered_system",
        "comparison": "comparison",
        "timeline": "linear_progression",
    }

    def __init__(self, validator: ArticleVisualSpecValidator | None = None) -> None:
        self.validator = validator or ArticleVisualSpecValidator()

    def plan(
        self,
        raw_spec: dict[str, Any],
        repository_full_name: str,
        allowed_evidence_paths: set[str] | None,
    ) -> ValidatedArticleVisualSpec:
        return self.validator.validate(
            raw_spec,
            repository_full_name,
            allowed_evidence_paths,
        )

    def plan_legacy_content_brief(
        self, item: dict[str, Any]
    ) -> ValidatedArticleVisualSpec:
        if not isinstance(item, dict):
            raise ArticleVisualSpecError("旧内容项必须是对象")
        repository_full_name = self._required_text(
            item.get("repository_full_name"), "repository_full_name"
        )
        summary_text = self._required_text(
            item.get("summary_text") or item.get("project_summary_text"),
            "summary_text",
        )
        raw_spec = self._build_legacy_summary_card(item, summary_text)
        return self.validator.validate(raw_spec, repository_full_name, None)

    def to_video_visual_brief(
        self, spec: ValidatedArticleVisualSpec
    ) -> dict[str, Any]:
        """派生旧视频链路所需字段，不根据总结或对比内容虚构关系。"""

        value = spec.value
        role = spec.figure_role
        nodes: list[dict[str, str]] = []
        relationships: list[dict[str, str]] = []
        if role == "flow":
            nodes = [self._video_node(item) for item in value["steps"]]
            relationships = [self._video_edge(item) for item in value["edges"]]
        elif role == "architecture":
            nodes = [self._video_node(item) for item in value["nodes"]]
            relationships = [self._video_edge(item) for item in value["edges"]]
        elif role == "timeline":
            nodes = [self._video_node(item) for item in value["events"]]

        node_ids = [node["id"] for node in nodes]
        if role == "summary_card":
            chinese_labels = [item["label"] for item in value["capabilities"]]
        elif role == "comparison":
            chinese_labels = [
                value["left"]["label"],
                value["right"]["label"],
                *[item["label"] for item in value["dimensions"]],
            ]
        else:
            chinese_labels = [node["label"] for node in nodes]
        return {
            # 保持旧视频链路识别的版本；静态图真实来源由 visual_spec 单独保存。
            "version": "creative_brief_v2",
            "teaching_goal": value["purpose"],
            "diagram_type": self._diagram_types[role],
            "visual_thesis": value["purpose"],
            "nodes": nodes,
            "relationships": relationships,
            "reading_order": node_ids,
            "chinese_labels": chinese_labels,
            "palette_key": "editorial_blue",
            "style": "notion",
            "negative_constraints": [],
        }

    def _build_legacy_summary_card(
        self,
        item: dict[str, Any],
        summary_text: str,
    ) -> dict[str, Any]:
        repository_full_name = self._required_text(
            item.get("repository_full_name"), "repository_full_name"
        )
        project_analysis_markdown = self._required_text(
            item.get("project_analysis_markdown"), "project_analysis_markdown"
        )
        section_cards = self._legacy_section_cards(project_analysis_markdown)
        return {
            "version": ARTICLE_VISUAL_SPEC_VERSION,
            "repository_full_name": repository_full_name,
            "figure_role": "summary_card",
            "purpose": "总结该项目的定位、机制和工程价值",
            "headline": repository_full_name.split("/", 1)[-1],
            "evidence_refs": [
                {
                    "kind": "generated_article",
                    "path": "project_analysis_markdown",
                    "claim": summary_text,
                }
            ],
            "positioning": summary_text,
            "capabilities": section_cards,
            "takeaways": self._legacy_takeaways(section_cards),
            "art_direction": {
                "style": "notion",
                "palette": "editorial_blue",
                "density": "medium",
            },
        }

    def _legacy_section_cards(self, markdown: str) -> list[dict[str, str]]:
        sections = self._extract_fixed_sections(markdown)
        cards: list[dict[str, str]] = []
        for section_name in self._legacy_sections:
            body = sections.get(section_name, "").strip()
            if not body:
                raise ArticleVisualSpecError(
                    f"project_analysis_markdown 缺少“{section_name}”正文"
                )
            cards.append(
                {"label": section_name, "description": self._first_sentence(body)}
            )
        return cards

    @staticmethod
    def _legacy_takeaways(cards: list[dict[str, str]]) -> list[str]:
        return [card["description"] for card in cards[:3]]

    def _extract_fixed_sections(self, markdown: str) -> dict[str, str]:
        sections: dict[str, list[str]] = {name: [] for name in self._legacy_sections}
        current: str | None = None
        heading_pattern = re.compile(
            r"^\s*(?:#{1,6}\s*)?(?:\*\*)?"
            r"(技术特点|机制拆解|工程启发)"
            r"(?:\*\*)?\s*(?:[:：])?\s*$"
        )
        generic_heading_pattern = re.compile(r"^\s*(?:#{1,6}\s+|\*\*.+\*\*\s*$)")
        for raw_line in markdown.splitlines():
            match = heading_pattern.match(raw_line)
            if match:
                current = match.group(1)
                continue
            if generic_heading_pattern.match(raw_line):
                current = None
                continue
            if current is not None and raw_line.strip():
                sections[current].append(raw_line.strip())
        return {name: " ".join(lines).strip() for name, lines in sections.items()}

    @staticmethod
    def _first_sentence(text: str) -> str:
        cleaned = re.sub(r"^\s*(?:[-*+]\s+|\d+[.)、]\s*)", "", text).strip()
        if not cleaned:
            raise ArticleVisualSpecError("固定章节的第一句不能为空")
        # 以自然语义标点保留完整首分句；容量仍由 validator 判断，绝不字符切片。
        match = re.match(r"^.*?[，,；;。！？!?：:]", cleaned)
        return (match.group(0) if match else cleaned).strip()

    @staticmethod
    def _video_node(item: dict[str, str]) -> dict[str, str]:
        return {
            "id": item["id"],
            "label": item["label"],
            "role": item.get("description", ""),
        }

    @staticmethod
    def _video_edge(item: dict[str, str]) -> dict[str, str]:
        edge = {"from": item["from"], "to": item["to"]}
        if item.get("label"):
            edge["label"] = item["label"]
        return edge

    @staticmethod
    def _required_text(raw: Any, field: str) -> str:
        if not isinstance(raw, str) or not raw.strip():
            raise ArticleVisualSpecError(f"{field} 不能为空")
        return raw.strip()


__all__ = ["ArticleVisualPlanningService"]
