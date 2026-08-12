from __future__ import annotations

import re
from typing import Any

from src.config.config_manager import AppConfig
from src.services.media_creative_brief_service import MediaCreativeBriefService


class ImagePromptDesignService:
    """把内容简报编译成可直接交给 Seedream 的中文技术教学图提示词。

    SummaryTask 只负责给出项目价值与 ``visual_brief``；本服务不猜测某个项目应当
    使用固定蓝色流程图，而是根据 diagram_type、节点、关系、阅读顺序和调色板生成
    一张独立的教学图。该服务不调用外部 API、不写数据库，也不做本地叠字，最终图像
    始终由火山方舟原始生成。
    """

    _whitespace_pattern = re.compile(r"\s+")
    _url_pattern = re.compile(r"https?://\S+", re.IGNORECASE)
    _repo_pattern = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")

    _palette_instructions = {
        "paper_cobalt_amber": (
            "暖白纸张与浅石墨纹理背景，主色为钴蓝，强调色为琥珀橙，"
            "辅以深灰文字和极浅蓝灰连线；颜色只用于信息层级，不铺满背景。"
        ),
        "paper_violet_coral": (
            "象牙白纸本背景，主色为深紫，强调色为珊瑚橙，辅以暖灰线条；"
            "整体克制、像编辑过的技术专栏配图。"
        ),
        "paper_teal_tangerine": (
            "米白纸张与细微网格背景，主色为深青绿，强调色为橘黄，辅以石墨灰；"
            "禁止蓝绿色霓虹和大面积渐变。"
        ),
        "paper_ink_lime": (
            "浅灰纸张背景，主色为墨黑与深灰，强调色为黄绿色，辅以低饱和蓝灰；"
            "像严谨的工程课程讲义，不要赛博科技海报。"
        ),
        "paper_navy_orange": (
            "暖白或浅米灰纸张背景，主色为海军蓝，强调色为工程橙，辅以灰蓝连线；"
            "色彩有明确功能分工，画面明亮但不过度装饰。"
        ),
    }

    _diagram_layouts = {
        "structural_breakdown": (
            "采用由整体到局部的结构拆解图：中心放核心机制，周围放少量支撑模块，"
            "以分组框和短箭头表达归属、调用或依赖；避免模板化的三栏输入—核心—输出布局。"
        ),
        "linear_progression": (
            "采用单条主流程的横向或斜向推进图：按阅读顺序逐个展开节点，"
            "每个节点只表达一个动作，流程线是视觉主角，辅助关系退到第二层。"
        ),
        "circular_flow": (
            "采用清晰的环形闭环图：核心节点可居中，环上节点按顺时针读序排布，"
            "回流箭头必须明确指向反馈或修正，不能画成随机旋转装饰。"
        ),
        "hub_spoke": (
            "采用中心辐射图：一个中心能力节点连接有限的周边能力点，"
            "用不同线型区分主路径和辅助连接，中心与周边之间保留足够留白。"
        ),
        "layered_system": (
            "采用分层堆栈图：按自下而上或自上而下的系统层级排布，"
            "层内模块对齐，层间用少量垂直箭头说明支撑或调用关系。"
        ),
        "comparison": (
            "采用左右对照的改造图：左边是旧做法或问题，右边是改造后的机制或结果，"
            "中间只放一个关键变化箭头，突出工程取舍，不能做成两张无关海报。"
        ),
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
        关系与配色，``focus_prompt``/``project_summary_text`` 用于补充工程语义。

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
        palette_key = str(brief.get("palette_key", "paper_cobalt_amber"))
        layout_instruction = self._diagram_layouts.get(
            diagram_type,
            self._diagram_layouts["structural_breakdown"],
        )
        palette_instruction = self._palette_instructions.get(
            palette_key,
            self._palette_instructions["paper_cobalt_amber"],
        )

        node_instruction = self._format_nodes(brief.get("nodes"))
        relationship_instruction = self._format_relationships(brief.get("relationships"), brief.get("nodes"))
        reading_instruction = self._format_reading_order(brief.get("reading_order"), brief.get("nodes"))
        label_instruction = self._format_labels(brief.get("chinese_labels"))
        negative_constraints = self._format_negative_constraints(brief.get("negative_constraints"))
        semantic_focus = cleaned_focus or cleaned_summary or str(brief.get("visual_thesis", "")).strip()

        parts = [
            "任务：生成一张 16:9 横版、面向中文技术读者的工程教学信息图。它是技术文章中的原生插图，不是抽象插画、产品广告、网页截图或电影海报。",
            f"教学目标：{brief.get('teaching_goal', '用一张图说明项目的工程机制与信息流向')}。",
            f"核心判断：{brief.get('visual_thesis', semantic_focus)}。",
            f"视觉体系：{self.config.image_prompt_visual_system}",
            f"本张图调色：{palette_instruction}",
            f"版式类型：{diagram_type}。{layout_instruction}",
            f"结构关系：{self.config.image_prompt_composition_rule}",
            f"节点设计：{node_instruction}",
            f"连接关系：{relationship_instruction}",
            f"阅读路径：{reading_instruction}",
            f"文字规范：{self.config.image_prompt_text_rule} 可出现的标签仅为：{label_instruction}。",
            f"构图与留白：{self.config.image_prompt_safe_zone_rule}",
            f"图形语言：{self.config.image_prompt_style_rule}",
            "生成方式约束：直接由火山方舟 Seedream 生成完整原始图像；禁止任何本地叠字、遮罩、拼贴、二次覆盖或后期把文字压到图上。",
            "可读性优先级：先让读者一眼看懂主关系，再呈现次级模块；标签使用 2 到 6 个汉字的短词，必要英文技术词只能作为短补充，不能出现长英文、仓库名、网址或代码段。",
            f"反向约束：{self.config.image_prompt_negative_prompt} {negative_constraints}",
        ]

        prompt = " ".join(part for part in parts if str(part).strip())
        return self._limit_text(prompt, max_length=self.config.image_prompt_max_length)

    def _format_nodes(self, raw_nodes: Any) -> str:
        if not isinstance(raw_nodes, list):
            return "仅保留 3 到 6 个语义明确的模块，模块大小按重要性分级。"
        nodes: list[str] = []
        for index, raw_node in enumerate(raw_nodes[:6], start=1):
            if not isinstance(raw_node, dict):
                continue
            label = self._short_text(raw_node.get("label"), 8)
            role = self._short_text(raw_node.get("role"), 24)
            if label:
                nodes.append(f"{index}号「{label}」{f'（{role}）' if role else ''}")
        return "、".join(nodes) or "仅保留 3 到 6 个语义明确的模块，模块大小按重要性分级。"

    def _format_relationships(self, raw_relationships: Any, raw_nodes: Any) -> str:
        if not isinstance(raw_relationships, list) or not isinstance(raw_nodes, list):
            return "只保留主链路与必要反馈箭头，避免复杂蜘蛛网连线。"
        label_map = {
            str(node.get("id", "")).strip(): self._short_text(node.get("label"), 8)
            for node in raw_nodes
            if isinstance(node, dict)
        }
        relationships: list[str] = []
        for raw_relation in raw_relationships[:7]:
            if not isinstance(raw_relation, dict):
                continue
            source = label_map.get(str(raw_relation.get("from", "")).strip(), "")
            target = label_map.get(str(raw_relation.get("to", "")).strip(), "")
            label = self._short_text(raw_relation.get("label"), 8) or "流转"
            if source and target:
                relationships.append(f"「{source}」经「{label}」指向「{target}」")
        return "；".join(relationships) or "只保留主链路与必要反馈箭头，避免复杂蜘蛛网连线。"

    def _format_reading_order(self, raw_order: Any, raw_nodes: Any) -> str:
        if not isinstance(raw_order, list) or not isinstance(raw_nodes, list):
            return "按主关系从左到右、从上到下或顺时针读取，不能让读者猜测起点。"
        label_map = {
            str(node.get("id", "")).strip(): self._short_text(node.get("label"), 8)
            for node in raw_nodes
            if isinstance(node, dict)
        }
        labels = [label_map.get(str(node_id).strip(), "") for node_id in raw_order]
        labels = [label for label in labels if label]
        if not labels:
            return "按主关系从左到右、从上到下或顺时针读取，不能让读者猜测起点。"
        return " → ".join(labels)

    def _format_labels(self, raw_labels: Any) -> str:
        if not isinstance(raw_labels, list):
            return "仅使用节点中的短中文标签，不额外制造长标题。"
        labels = [self._short_text(item, 8) for item in raw_labels[:8]]
        labels = [label for label in labels if label]
        return "、".join(labels) if labels else "仅使用节点中的短中文标签，不额外制造长标题。"

    def _format_negative_constraints(self, raw_constraints: Any) -> str:
        if not isinstance(raw_constraints, list):
            return "不要伪文字、乱码、长英文、网址、仓库名或真实品牌标识。"
        constraints = [self._compact_text(str(item), 80) for item in raw_constraints[:6]]
        constraints = [item for item in constraints if item]
        return "；".join(constraints) or "不要伪文字、乱码、长英文、网址、仓库名或真实品牌标识。"

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
