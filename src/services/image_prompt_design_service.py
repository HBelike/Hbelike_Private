from __future__ import annotations

import re
from typing import Any

from src.config.config_manager import AppConfig
from src.services.media_creative_brief_service import MediaCreativeBriefService


class ImagePromptDesignService:
    """把内容简报编译成可直接交给 Seedream 的中文技术教学图提示词。

    SummaryTask 只负责给出项目价值与 ``visual_brief``；本服务不猜测某个项目应当
    使用通用蓝色流程图，而是根据 diagram_type、真实节点和关系生成一张独立的
    白底商务技术 PPT 教学图。该服务不调用外部 API、不写数据库，也不做本地叠字，最终图像
    始终由火山方舟原始生成。
    """

    _whitespace_pattern = re.compile(r"\s+")
    _url_pattern = re.compile(r"https?://\S+", re.IGNORECASE)
    _repo_pattern = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")

    _compact_diagram_layouts = {
        "structural_breakdown": "分区结构拆解，组件边界清楚",
        "linear_progression": "自左向右单主线流程",
        "circular_flow": "顺时针闭环，回流方向明确",
        "hub_spoke": "中心模块连接周边组件",
        "layered_system": "自下而上分层，层间垂直连接",
        "comparison": "左右对照，中间突出关键变化",
    }

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._brief_service = MediaCreativeBriefService()

    def build_project_architecture_prompt(
        self,
        repository_full_name: str,
        focus_prompt: str,
        project_summary_text: str = "",
        visual_brief: dict[str, Any] | None = None,
        project_index: int = 1,
    ) -> str:
        """生成一段可直接提交给 Ark Seedream 的最终架构图提示词。

        输入：项目名仅用于清理文本和确定性兜底；``visual_brief`` 给出图表类型、节点、
        关系，``focus_prompt``/``project_summary_text`` 用于补充工程语义。

        输出：长度受 ``image.prompt.max_length`` 控制的中文提示词。

        失败处理：不访问网络；不完整的简报由 ``MediaCreativeBriefService`` 补全。
        线程安全：不持有跨请求可变状态。
        """

        cleaned_focus = self._sanitize_visual_text(focus_prompt, repository_full_name, max_length=260)
        cleaned_summary = self._sanitize_visual_text(project_summary_text, repository_full_name, max_length=260)
        brief = self._brief_service.normalize_visual_brief(
            raw_brief=visual_brief,
            repository_full_name=repository_full_name,
            fallback_text=cleaned_focus or cleaned_summary,
            project_index=project_index,
        )

        diagram_type = str(brief.get("diagram_type", "structural_breakdown"))
        layout_instruction = self._compact_diagram_layouts.get(
            diagram_type,
            self._compact_diagram_layouts["structural_breakdown"],
        )

        node_instruction = self._format_nodes(brief.get("nodes"))
        relationship_instruction = self._format_relationships(brief.get("relationships"), brief.get("nodes"))
        semantic_focus = self._compact_text(
            str(brief.get("visual_thesis", "")).strip() or cleaned_focus or cleaned_summary,
            max_length=42,
        )

        # 官方建议中文图片 prompt 不超过 300 字。这里保留“风格、结论、模块、关系、
        # 文字禁区”五类高优先级信息，不再重复堆叠全局说明书。
        style_contract = self._compact_text(self.config.image_prompt_visual_system, max_length=72)
        body_parts = [
            style_contract,
            f"核心结论：{semantic_focus}。" if semantic_focus else "",
            f"布局：{layout_instruction}；规整矩形模块分区，位置对齐，线条不交叉。",
            f"模块文字仅限：{node_instruction}。",
            f"黑色实线正交折线箭头，严格禁用曲线：{relationship_instruction}。",
        ]
        terminal_contract = (
            "只显示上述模块名和连线标签，字体规整清晰且不重叠；"
            "禁标题、正文、页码、人物、照片、logo、水印、额外文字、伪文字和乱码。"
        )
        prompt = " ".join(part for part in body_parts if part)
        available = max(1, self.config.image_prompt_max_length - len(terminal_contract) - 1)
        prompt = self._limit_text(prompt, max_length=available)
        return f"{prompt} {terminal_contract}".strip()

    def _format_nodes(self, raw_nodes: Any) -> str:
        if not isinstance(raw_nodes, list):
            return "3到4个语义明确的短标签"
        nodes: list[str] = []
        for raw_node in raw_nodes[:4]:
            if not isinstance(raw_node, dict):
                continue
            label = self._short_text(raw_node.get("label"), 8)
            if label:
                nodes.append(f"「{label}」")
        return "、".join(nodes) or "3到4个语义明确的短标签"

    def _format_relationships(self, raw_relationships: Any, raw_nodes: Any) -> str:
        if not isinstance(raw_relationships, list) or not isinstance(raw_nodes, list):
            return "只保留主链路与必要反馈箭头，避免复杂蜘蛛网连线。"
        label_map = {
            str(node.get("id", "")).strip(): self._short_text(node.get("label"), 8)
            for node in raw_nodes
            if isinstance(node, dict)
        }
        relationships: list[str] = []
        for raw_relation in raw_relationships[:4]:
            if not isinstance(raw_relation, dict):
                continue
            source = label_map.get(str(raw_relation.get("from", "")).strip(), "")
            target = label_map.get(str(raw_relation.get("to", "")).strip(), "")
            label = self._short_text(raw_relation.get("label"), 8) or "流转"
            if source and target:
                relationships.append(f"「{source}」—{label}→「{target}」")
        return "；".join(relationships) or "只保留主数据流"

    def _sanitize_visual_text(self, text: str, repository_full_name: str, max_length: int) -> str:
        """清理不适合进入图像提示词的 URL、仓库名和格式噪声。"""

        normalized = text or ""
        normalized = self._url_pattern.sub("", normalized)
        normalized = normalized.replace(repository_full_name, "该项目")
        for part in repository_full_name.split("/", 1):
            if len(part) >= 3:
                normalized = normalized.replace(part, "")
        normalized = self._repo_pattern.sub("该项目", normalized)
        normalized = normalized.replace("`", "").replace("*", "").replace("#", "")
        normalized = normalized.replace("<", "").replace(">", "")
        return self._compact_text(normalized, max_length=max_length).strip(" ，,。；;：:")

    def _short_text(self, value: Any, max_length: int) -> str:
        return self._compact_text(str(value or ""), max_length=max_length).strip(" ，,。；;：:")

    def _compact_text(self, text: str, max_length: int) -> str:
        compacted = self._whitespace_pattern.sub(" ", text.replace("\r", " ").replace("\n", " ")).strip()
        return self._limit_text(compacted, max_length=max_length)

    def _limit_text(self, text: str, max_length: int) -> str:
        """按配置长度截断提示词，避免运行时附加规则稀释核心约束。"""

        normalized = text.strip()
        if len(normalized) <= max_length:
            return normalized
        return normalized[: max(1, max_length - 1)].rstrip(" ，,；;。") + "。"
