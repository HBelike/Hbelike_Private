from __future__ import annotations

import re

from src.config.config_manager import AppConfig


class ImagePromptDesignService:
    """把业务语义 prompt 转成更稳定的 Seedream 技术课件图 prompt。

    这层服务不直接调用生图模型，只负责把 SummaryTask 产出的业务描述
    改写成适合图片模型理解的“画面导演指令”。
    """

    _whitespace_pattern = re.compile(r"\s+")
    _url_pattern = re.compile(r"https?://\S+")
    _repo_pattern = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")

    def __init__(self, config: AppConfig) -> None:
        self.config = config

    def build_project_architecture_prompt(
        self,
        repository_full_name: str,
        focus_prompt: str,
        project_summary_text: str = "",
    ) -> str:
        """生成单个 GitHub 项目的技术教学风架构图 prompt。

        输入：
        - repository_full_name：GitHub 仓库全名，只用于识别项目类型，不直接画到图里。
        - focus_prompt：SummaryTask 生成的项目视觉重点。
        - project_summary_text：项目概要，用于辅助确定画面模块。

        输出：
        - 一段适合 Seedream 的中文生图 prompt。

        失败处理：
        - 本函数不访问外部资源，不抛业务异常；未知项目会走通用课件模板。

        线程安全：
        - 无共享可变状态，线程安全。

        任务监控：
        - 不直接更新任务状态，由调用它的 ImageTask 记录任务运行结果。
        """

        known_project_brief = self._known_project_visual_brief(repository_full_name)
        if known_project_brief:
            project_brief = known_project_brief
        else:
            project_brief = self._build_generic_project_brief(
                repository_full_name=repository_full_name,
                focus_prompt=focus_prompt,
                project_summary_text=project_summary_text,
            )

        parts = [
            "生成一张16:9横版技术博客架构图，画面要像源码阅读文章里嵌入的工程结构图，而不是抽象插画或宣传海报。",
            f"视觉系统：{self.config.image_prompt_visual_system}",
            f"构图规则：{self.config.image_prompt_composition_rule}",
            f"文字规则：{self.config.image_prompt_text_rule}",
            f"安全区：{self.config.image_prompt_safe_zone_rule}",
            f"本项目画面方案：{project_brief}",
            f"风格指令：{self.config.image_prompt_style_rule}",
            f"反向约束：{self.config.image_prompt_negative_prompt}",
            "特别强调：标签必须短、大、清楚；宁可减少标签，也不要生成乱码、小字、伪文字或无法辨认的字符。",
        ]

        prompt = " ".join(part for part in parts if part.strip())
        return self._limit_text(prompt, max_length=self.config.image_prompt_max_length)

    def _known_project_visual_brief(self, repository_full_name: str) -> str:
        """为已知周榜项目提供明确的课件式版式。

        这里不使用仓库地址和长项目名，避免图片里出现难看的英文长串。
        """

        repo = repository_full_name.lower()
        if repo == "mattpocock/skills":
            return (
                "白色背景，顶部居中蓝色胶囊标题“技能分层加载”。"
                "左侧三层浅蓝线框模块：元数据层、指令层、资源层。"
                "中间放一个大圆角容器“Skill Runtime”，内部模块为“名称”“描述”“SKILL.md”“Reference”“Script”。"
                "右侧放两个蓝色状态卡：“始终加载”“按需加载”。"
                "用蓝色箭头表现从元数据到指令再到资源的渐进披露关系。"
            )
        if repo == "graphify-labs/graphify":
            return (
                "白色背景，顶部蓝色胶囊标题“代码图谱生成”。"
                "左侧三张输入卡：代码、文档、数据库。"
                "中间大卡片写“解析引擎”，内部三步为“抽取实体”“识别关系”“建立索引”。"
                "右侧输出卡写“可查询图谱”，旁边只画6到8个蓝色节点和少量连线。"
                "箭头从输入流向解析引擎，再流向图谱。"
            )
        if repo == "codecrafters-io/build-your-own-x":
            return (
                "白色背景，顶部蓝色胶囊标题“从零构建技术”。"
                "中间是一条横向学习流水线：经典系统、拆解、实现、测试、理解底层。"
                "每一步都是蓝色线框圆角卡片，卡片内放简洁图标和短中文标签。"
                "底部用浅蓝虚线补充“练习项目”“反馈修正”两个输入，不要画成杂乱工具箱。"
            )
        if repo == "nousresearch/hermes-agent":
            return (
                "白色背景，顶部蓝色胶囊标题“可成长Agent”。"
                "中心是蓝色卡片“策略更新”，外圈画清晰循环箭头。"
                "循环节点依次为：任务、计划、执行、反馈、记忆。"
                "右侧输出卡写“能力成长”。"
                "整体像 agent loop 架构图，不要画成人形机器人海报。"
            )
        if repo == "anomalyco/opencode":
            return (
                "白色背景，顶部蓝色胶囊标题“开放编码代理”。"
                "左侧输入卡为：代码上下文、用户目标、运行反馈。"
                "中间大卡片写“代理决策”，内部三步为“理解”“修改”“验证”。"
                "右侧输出卡写“可控补丁”。"
                "用蓝色箭头表现上下文进入代理决策，再输出补丁和反馈闭环。"
            )
        return ""

    def _build_generic_project_brief(
        self,
        repository_full_name: str,
        focus_prompt: str,
        project_summary_text: str,
    ) -> str:
        """未知项目的通用课件图方案。

        通用方案会保留 SummaryTask 的核心机制，但去掉仓库名、URL 和过长文本，
        防止模型把地址、英文长串或代码画进图片。
        """

        focus = self._sanitize_visual_text(focus_prompt, repository_full_name, max_length=180)
        summary = self._sanitize_visual_text(project_summary_text, repository_full_name, max_length=160)
        source_text = focus or summary or "围绕项目的输入、核心处理模块和输出价值，做一张三段式技术架构图。"

        return (
            "顶部使用蓝色胶囊标题，标题写项目的核心机制名，不要使用仓库名。"
            "左侧放2到3个浅蓝输入/问题卡，中间放2到3个蓝色线框核心模块，"
            "右侧放1个结果/价值卡；用清晰蓝色箭头连接。"
            f"画面内容依据：{source_text}"
        )

    def _sanitize_visual_text(self, text: str, repository_full_name: str, max_length: int) -> str:
        """清理不适合进入图片 prompt 的文本。

        只去掉 URL、仓库全名、代码符号和多余空白，不再把所有文字都抹掉，
        因为当前目标是生成带短中文标签的教学课件图。
        """

        normalized = text or ""
        normalized = self._url_pattern.sub("", normalized)
        normalized = normalized.replace(repository_full_name, "该项目")
        repository_parts = repository_full_name.split("/", 1)
        if len(repository_parts) == 2:
            owner, repo = repository_parts
            normalized = normalized.replace(owner, "")
            normalized = normalized.replace(repo, "")
        normalized = self._repo_pattern.sub("该项目", normalized)
        normalized = normalized.replace("`", "").replace("*", "").replace("#", "")
        normalized = normalized.replace("<", "").replace(">", "")
        normalized = self._compact_text(normalized, max_length=max_length)
        return normalized.strip(" ，,。；;：:")

    def _compact_text(self, text: str, max_length: int) -> str:
        """压缩空白并截断文本，避免超长 prompt 稀释关键视觉约束。"""

        compacted = self._whitespace_pattern.sub(" ", text.replace("\r", " ").replace("\n", " ")).strip()
        return self._limit_text(compacted, max_length=max_length)

    def _limit_text(self, text: str, max_length: int) -> str:
        """按配置长度截断 prompt，截断时尽量保留完整语义。"""

        normalized = text.strip()
        if len(normalized) <= max_length:
            return normalized
        return normalized[: max_length - 1].rstrip(" ，,；;。") + "。"
