from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from json import JSONDecodeError
from typing import Any

from src.providers.deepseek_provider import DeepSeekMessage, DeepSeekProvider, parse_json_object_from_text
from src.repositories.content_approval_repository import ContentApprovalRepository
from src.repositories.generated_content_repository import GeneratedContentInput, GeneratedContentRepository
from src.repositories.weekly_ranking_repository import WeeklyRankingRecord, WeeklyRankingRepository
from src.tasks.base_task import BaseTask
from src.tasks.task_context import TaskContext


class SummaryTask(BaseTask):
    """负责分析可配置数量的周榜项目，并生成公众号图文和视频脚本。"""

    task_name = "SummaryTask"
    _repository_token_pattern = re.compile(r"(?<![\w.-])([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?![\w.-])")
    _banned_ai_style_phrases = (
        "在当今",
        "在这个快速发展的时代",
        "随着人工智能的快速发展",
        "让我们深入了解",
        "让我们一起来看看",
        "我们",
        "咱们",
        "小编",
        "笔者",
        "解锁",
        "赋能",
        "颠覆",
        "无论你是资深开发者还是初学者",
        "不只是",
        "更是",
    )
    def execute(self, context: TaskContext) -> dict[str, Any]:
        """读取最新周榜，调用 DeepSeek 生成结构化内容并入库。"""
        ranking_repository = WeeklyRankingRepository(database_manager=context.database_manager)
        content_repository = GeneratedContentRepository(database_manager=context.database_manager)
        approval_repository = ContentApprovalRepository(database_manager=context.database_manager)

        week_end = ranking_repository.latest_week_end()
        if week_end is None:
            raise RuntimeError("没有可总结的 weekly_rankings，请先运行 SearchTask")

        rankings = ranking_repository.list_for_week(week_end)
        if not rankings:
            raise RuntimeError(f"week_end={week_end} 没有周榜记录")

        highest_star_repository = max(rankings, key=lambda item: item.current_stars)
        regeneration_feedback = self._latest_regeneration_feedback(
            content_repository=content_repository,
            approval_repository=approval_repository,
        )
        provider = DeepSeekProvider(config=context.config, run_name="wechat.summary.generate")
        response, normalized = self._generate_normalized_content(
            provider=provider,
            rankings=rankings,
            week_end=week_end,
            highest_star_repository=highest_star_repository,
            regeneration_feedback=regeneration_feedback,
            summary_instruction=context.config.runtime_prompt("summary"),
        )

        record = content_repository.create(
            GeneratedContentInput(
                week_end=week_end,
                title=normalized["title"],
                digest=normalized["digest"],
                article_markdown=normalized["article_markdown"],
                video_script=normalized["video_script"],
                voiceover_text=normalized["voiceover_text"],
                image_prompts=normalized["image_prompts"],
                raw_response={
                    "model": response.model,
                    "parsed": normalized,
                    "raw": response.raw_response,
                },
            )
        )

        self.logger.info(
            "周榜总结生成完成：week_end=%s content_id=%s title=%s",
            week_end,
            record.id,
            record.title,
        )

        return {
            "week_end": week_end,
            "content_id": record.id,
            "title": record.title,
            "status": record.status,
            "model": response.model,
            "ranking_count": len(rankings),
            "image_prompt_count": len(normalized["image_prompts"]),
            "highest_star_repository": highest_star_repository.full_name,
            "github_url_in_article": self._contains_github_url(normalized["article_markdown"]),
            "first_person_in_article": "我们" in normalized["article_markdown"] or "咱们" in normalized[
                "article_markdown"],
            "regeneration_feedback_applied": bool(regeneration_feedback),
        }

    def _latest_regeneration_feedback(
        self,
        content_repository: GeneratedContentRepository,
        approval_repository: ContentApprovalRepository,
    ) -> str | None:
        """读取最近一条被驳回内容的人工反馈，供下一次生成使用。"""

        latest_content = content_repository.latest_for_preview()
        if latest_content is None:
            return None

        approval = approval_repository.latest_regeneration_feedback_for_content(latest_content.id)
        if approval is None or not approval.comment:
            return None

        return approval.comment.strip()

    def _generate_normalized_content(
            self,
            provider: DeepSeekProvider,
            rankings: list[WeeklyRankingRecord],
            week_end: str,
            highest_star_repository: WeeklyRankingRecord,
            regeneration_feedback: str | None = None,
            summary_instruction: str = "",
    ) -> tuple[Any, dict[str, Any]]:
        """调用 DeepSeek 并解析结果；格式异常时自动重试一次短版请求。"""
        attempts = [
            self._build_messages(
                rankings,
                week_end,
                highest_star_repository,
                regeneration_feedback,
                summary_instruction,
            ),
            self._build_retry_messages(
                rankings,
                week_end,
                highest_star_repository,
                regeneration_feedback,
                summary_instruction,
            ),
        ]
        last_error: Exception | None = None

        for attempt_index, messages in enumerate(attempts, start=1):
            response = provider.chat(messages)
            try:
                parsed = parse_json_object_from_text(response.content)
                normalized = self._normalize_model_output(parsed, rankings, highest_star_repository)
                if attempt_index > 1:
                    self.logger.info("DeepSeek 第 %s 次尝试输出了可解析 JSON", attempt_index)
                return response, normalized
            except (JSONDecodeError, ValueError) as exc:
                last_error = exc
                if attempt_index < len(attempts):
                    self.logger.warning("DeepSeek 第 %s 次输出格式异常，将重试短版 JSON：%s", attempt_index, exc)
                    continue
                raise

        raise RuntimeError("DeepSeek 内容生成失败") from last_error

    def _build_messages(
            self,
            rankings: list[WeeklyRankingRecord],
            week_end: str,
            highest_star_repository: WeeklyRankingRecord,
            regeneration_feedback: str | None = None,
            summary_instruction: str = "",
    ) -> list[DeepSeekMessage]:
        """构造 DeepSeek 消息。"""
        ranking_payload = [
            {
                "rank": item.rank,
                "full_name": item.full_name,
                "description": item.description,
                "language": item.language,
                "current_stars": item.current_stars,
                "star_growth": item.star_growth,
                "growth_rate": item.growth_rate,
                "score": item.score,
                "reason": item.reason,
            }
            for item in rankings
        ]
        writing_playbook = self._build_writing_playbook_prompt()
        regeneration_feedback_section = self._build_regeneration_feedback_section(regeneration_feedback)
        project_count = len(rankings)
        project_section_title = f"### Top {project_count} 项目拆解"
        required_project_headings = "、".join(
            f"#### 项目 {index}：{item.full_name}"
            for index, item in enumerate(rankings, start=1)
        )
        timeline_markers = "、".join(self._timeline_markers(project_count))
        summary_instruction_section = self._build_runtime_instruction_section(
            title="管理员摘要指令",
            instruction=summary_instruction,
        )
        system_prompt = (
            "你是一名具有丰富工程经验的技术传播者、Agent 开发专家和技术博客主笔。"
            "你的读者是有一定工程背景、但没有时间逐个翻仓库的开发者。"
            "你必须把 GitHub 周榜写成一篇有判断、有证据、有节奏的中文技术博客，而不是把五个项目机械罗列。"
            "所有判断都必须来自输入数据；信息不足时要明确使用审慎措辞。"
            "全文不得使用第一人称或账号自称，尤其不要出现“我们”“咱们”“小编”“笔者”。"
        )
        user_prompt = f"""
请基于以下 GitHub 周榜 Top {project_count} 数据，生成微信公众号文章和视频脚本。

写作方法：
{writing_playbook}

{regeneration_feedback_section}

{summary_instruction_section}

硬性要求：
1. 只输出一个 JSON 对象，不要输出 Markdown 代码块。
2. JSON 字段必须包含：title、digest、article_markdown、video_script、voiceover_text、image_prompts。
3. image_prompts 必须是数组，数量必须等于 {project_count}；每项包含 repository_full_name、prompt、summary_text。
4. article_markdown 不允许出现任何 GitHub 仓库地址、URL 或“项目地址”段落；只保留仓库名、stars、本周增长、增长率等信息。
5. article_markdown 的第一段必须是强钩子：用具体数字、反常识判断、读者痛点或开放问题引出，不允许用“本周 GitHub 热门项目来了”这种流水账开头。
6. article_markdown 必须包含这些 Markdown 小标题：### 本周主线、{project_section_title}、### 工程启发。
7. {project_section_title} 下必须按固定格式写 {project_count} 个四级标题：{required_project_headings}。
8. 每个项目都要讲清楚：项目定位、解决什么问题、技术机制、适合谁、潜在局限；不要写 GitHub 地址。
9. video_script 必须按 {project_count + 2} 段时间轴输出，段落标题必须依次包含：{timeline_markers}。
10. voiceover_text 必须是一段可直接配音的约 60 秒中文口播，按“开场趋势 → 项目 1 到 {project_count} → 结尾 CTA”自然推进。
11. image_prompts 必须生成 {project_count} 张“技术架构图/流程图”提示词，每张只服务一个项目，不要做抽象海报。
12. 每个 summary_text 控制在 70 字以内，必须与对应图片一对一匹配，用一句话解释“这张图在辅助理解什么”。
13. 禁止使用空泛 AI 套话，例如：在当今、赋能、解锁、颠覆、让我们深入了解、我们、不只是 X 更是 Y。
14. 不要编造项目不存在的能力；如果数据不足，用“从仓库描述看”“更像是”“可能适合”这种审慎表达。
15. 文风参考技术教学科普视频：先讲现象，再讲项目价值，再讲工程启发；句子短，信息密度高，有判断但不过度营销。
16. 控制输出长度：title 34 字以内，digest 110 字以内，article_markdown 1900 字以内，video_script 950 字以内，voiceover_text 650 字以内。
17. 每个 image prompt 控制在 190 字以内，必须让火山方舟直接生成可用原图：白底技术博客架构图、蓝色线框/图标、清晰模块、可出现少量简短中文词和必要英文技术词；不要英文长段落、不要仓库地址、不要 URL、不要水印。
18. image_prompts 中的 repository_full_name 必须严格照抄 Top {project_count} 数据里的 full_name，不能改写、缩写或拼错。

质量自检：
- 标题要有信息差或判断，不要只是“GitHub 热门项目 Top5”。
- 每个项目至少包含一个具体数字：stars、本周增长、增长率或排名。
- 每个项目必须把功能翻译成开发者收益，而不是只复述 description。
- 至少一次说明“为什么这个项目现在值得关注”。
- 至少一次说明“不要在什么场景里误用它”。
- 结尾必须给出工程启发，不要只说“欢迎关注”。
- 全文自检并删除“我们”“咱们”“项目地址”“GitHub 地址”和所有 URL。

week_end: {week_end}
stars 数量最多项目: {highest_star_repository.full_name}

Top {project_count} 数据：
{json.dumps(ranking_payload, ensure_ascii=False, indent=2)}
"""
        return [
            DeepSeekMessage(role="system", content=system_prompt),
            DeepSeekMessage(role="user", content=user_prompt.strip()),
        ]

    def _build_retry_messages(
            self,
            rankings: list[WeeklyRankingRecord],
            week_end: str,
            highest_star_repository: WeeklyRankingRecord,
            regeneration_feedback: str | None = None,
            summary_instruction: str = "",
    ) -> list[DeepSeekMessage]:
        """构造更短、更严格的重试消息，降低 JSON 截断概率。"""
        ranking_payload = [
            {
                "rank": item.rank,
                "full_name": item.full_name,
                "description": item.description,
                "language": item.language,
                "stars": item.current_stars,
                "growth": item.star_growth,
                "growth_rate": item.growth_rate,
            }
            for item in rankings
        ]
        system_prompt = "你只输出合法 JSON 对象。不要 Markdown，不要解释，不要换成数组。全文禁止使用“我们”“咱们”，禁止输出 URL。"
        regeneration_feedback_section = self._build_regeneration_feedback_section(regeneration_feedback)
        project_count = len(rankings)
        project_section_title = f"### Top {project_count} 项目拆解"
        required_project_headings = " 到 ".join(
            (
                f"#### 项目 1：{rankings[0].full_name}",
                f"#### 项目 {project_count}：{rankings[-1].full_name}",
            )
        )
        timeline_markers = "、".join(self._timeline_markers(project_count))
        summary_instruction_section = self._build_runtime_instruction_section(
            title="管理员摘要指令",
            instruction=summary_instruction,
        )
        user_prompt = f"""
重新生成一份更短的公众号周榜内容。

{regeneration_feedback_section}

{summary_instruction_section}

字段必须只有：
title: 32字以内字符串，要有信息差或明确判断
digest: 100字以内字符串，用一条趋势主线概括本期
article_markdown: 1600字以内字符串，必须包含 ### 本周主线、{project_section_title}、### 工程启发；{project_section_title} 下必须有 {required_project_headings}；不要输出任何 URL 或项目地址
video_script: 850字以内字符串，必须包含 {timeline_markers} 共 {project_count + 2} 段
voiceover_text: 600字以内字符串，可直接配音，短句递进
image_prompts: {project_count}项数组，每项包含 repository_full_name、prompt、summary_text；prompt 180字以内，必须是火山方舟直接生成的白底蓝线技术博客架构图提示词，不要英文长段落、仓库地址、URL、水印；summary_text 70字以内，可直接作为图片下方图注；repository_full_name 必须逐项严格等于 Top {project_count} 的 full_name

week_end: {week_end}
Top {project_count}:
{json.dumps(ranking_payload, ensure_ascii=False)}
"""
        return [
            DeepSeekMessage(role="system", content=system_prompt),
            DeepSeekMessage(role="user", content=user_prompt.strip()),
        ]

    def _normalize_model_output(
            self,
            parsed: dict[str, Any],
            rankings: list[WeeklyRankingRecord],
            highest_star_repository: WeeklyRankingRecord,
    ) -> dict[str, Any]:
        """校验并规范化模型输出。"""
        required_fields = [
            "title",
            "digest",
            "article_markdown",
            "video_script",
            "voiceover_text",
            "image_prompts",
        ]
        for field in required_fields:
            if field not in parsed:
                raise ValueError(f"DeepSeek 输出缺少字段：{field}")

        title = str(parsed["title"]).strip()
        digest = str(parsed["digest"]).strip()
        article_markdown = str(parsed["article_markdown"]).strip()
        video_script = str(parsed["video_script"]).strip()
        voiceover_text = str(parsed["voiceover_text"]).strip()
        image_prompts = parsed["image_prompts"]

        if not isinstance(image_prompts, list):
            raise ValueError("DeepSeek 输出 image_prompts 必须是数组")

        if len(image_prompts) != len(rankings):
            raise ValueError(f"DeepSeek 输出 image_prompts 数量必须为 {len(rankings)}")

        expected_repository_names = [item.full_name for item in rankings]
        repository_aliases: dict[str, str] = {}
        repository_text_candidates = [title, digest, article_markdown, video_script, voiceover_text]
        for item in image_prompts:
            if not isinstance(item, dict):
                continue
            repository_text_candidates.append(str(item.get("repository_full_name", "")).strip())
            repository_text_candidates.append(str(item.get("prompt", "")).strip())

        repository_aliases.update(
            self._detect_repository_aliases_from_texts(
                texts=repository_text_candidates,
                expected_repository_names=expected_repository_names,
            )
        )

        normalized_prompts: list[dict[str, Any]] = []
        for index, item in enumerate(image_prompts):
            if not isinstance(item, dict):
                raise ValueError("image_prompts 的每一项必须是对象")
            expected_repository_full_name = expected_repository_names[index]
            raw_repository_full_name = str(item.get("repository_full_name", "")).strip()
            prompt = str(item.get("prompt", "")).strip()
            summary_text = str(item.get("summary_text", "") or item.get("project_summary_text", "")).strip()
            if not prompt:
                raise ValueError("image_prompts 每项必须包含 prompt")
            if not summary_text:
                summary_text = self._build_fallback_project_summary(rankings[index])
            if raw_repository_full_name != expected_repository_full_name:
                if raw_repository_full_name:
                    repository_aliases[raw_repository_full_name] = expected_repository_full_name
                self.logger.warning(
                    "DeepSeek image_prompts 仓库名与周榜不一致，已按排名纠偏：index=%s raw=%s expected=%s",
                    index + 1,
                    raw_repository_full_name or "<empty>",
                    expected_repository_full_name,
                )
            prompt = self._replace_repository_aliases(prompt, repository_aliases)
            if raw_repository_full_name:
                prompt = prompt.replace(raw_repository_full_name, expected_repository_full_name)
            prompt = self._normalize_author_voice(self._remove_github_urls_and_link_sections(prompt))
            normalized_prompts.append(
                {
                    "repository_full_name": expected_repository_full_name,
                    "prompt": prompt,
                    "summary_text": self._normalize_author_voice(
                        self._remove_github_urls_and_link_sections(summary_text)
                    )[:90],
                    "rank": rankings[index].rank,
                }
            )

        title = self._replace_repository_aliases(title, repository_aliases)
        digest = self._replace_repository_aliases(digest, repository_aliases)
        article_markdown = self._replace_repository_aliases(article_markdown, repository_aliases)
        video_script = self._replace_repository_aliases(video_script, repository_aliases)
        voiceover_text = self._replace_repository_aliases(voiceover_text, repository_aliases)
        title = self._normalize_author_voice(self._remove_github_urls_and_link_sections(title))
        digest = self._normalize_author_voice(self._remove_github_urls_and_link_sections(digest))
        article_markdown = self._normalize_author_voice(
            self._remove_github_urls_and_link_sections(article_markdown)
        )
        video_script = self._normalize_author_voice(self._remove_github_urls_and_link_sections(video_script))
        voiceover_text = self._normalize_author_voice(self._remove_github_urls_and_link_sections(voiceover_text))

        self._log_content_quality_warnings(
            title=title,
            digest=digest,
            article_markdown=article_markdown,
            video_script=video_script,
            voiceover_text=voiceover_text,
            project_count=len(rankings),
        )

        return {
            "title": title,
            "digest": digest,
            "article_markdown": article_markdown,
            "video_script": video_script,
            "voiceover_text": voiceover_text,
            "image_prompts": normalized_prompts,
        }

    def _build_regeneration_feedback_section(self, regeneration_feedback: str | None) -> str:
        """把人工审核意见转换成可追加到 LLM 请求里的修改要求。"""

        if not regeneration_feedback:
            return ""

        normalized_feedback = re.sub(r"\s+", " ", regeneration_feedback).strip()
        if not normalized_feedback:
            return ""

        return (
            "人工审核反馈：\n"
            f"{normalized_feedback[:600]}\n"
            "请优先修正上述问题；如果反馈与硬性要求冲突，以硬性要求为准。"
        )

    def _build_runtime_instruction_section(self, title: str, instruction: str) -> str:
        """追加管理员配置的提示词，并限制长度避免污染主提示词。"""

        normalized_instruction = re.sub(r"\s+", " ", instruction or "").strip()
        if not normalized_instruction:
            return ""
        return f"{title}：\n{normalized_instruction[:4000]}\n请在不违反硬性要求的前提下执行。"

    def _timeline_markers(self, project_count: int) -> list[str]:
        """根据项目数生成约 60 秒的确定性时间轴。"""

        project_count = max(1, project_count)
        durations = [5, *self._project_scene_durations(project_count), 5]
        markers: list[str] = []
        start = 0
        for duration in durations:
            end = start + duration
            markers.append(f"{start}-{end}s")
            start = end
        return markers

    def _project_scene_durations(self, project_count: int) -> list[int]:
        """把 50 秒项目讲解时长均匀分给本期项目。"""

        base_duration, remainder = divmod(50, max(1, project_count))
        return [base_duration + (1 if index < remainder else 0) for index in range(project_count)]

    def _build_writing_playbook_prompt(self) -> str:
        """生成 SummaryTask 专用写作准则，避免公众号正文变成平铺直叙的列表。"""

        return """
- 开场钩子：第一句只做一件事，让读者愿意看第二句。优先使用“具体数字 + 反常识”“开发者痛点 + 转折”“开放问题 + 本周数据”。
- 技术博客骨架：先交代读者为什么要关心，再解释项目解决的问题、核心机制、适用场景、限制与权衡。
- 推文线程节奏：一段只讲一个观点；先给判断，再给证据；用短句和换行控制阅读速度。
- 源码阅读感：如果输入数据里能看出模块、函数、目录、协议或工具链，请用反引号标出关键对象，例如 `agent-loop`、`runLoop()`、`packages/agent`；不要滥用代码样式，一段最多 2 个。
- 文章排版感：正文要像技术博客笔记，不要像产品发布稿。优先使用自然段推进，列表只用于拆模块或列边界；图片出现前后要有一句解释它在解决什么理解问题。
- 图片配合：每个项目的 summary_text 要能直接放在图片下方当图注，说明“这张图解释了什么关系”，不要写宣传语。
- 周榜主线：先提炼五个项目背后的共同趋势，例如“Agent 开发正在从框架走向可组合技能”“代码生成工具开始贴近真实工程流”等。
- 标题策略：标题必须有信息差、数字或明确判断，但不能承诺输入数据没有支持的结果。
- 读者收益：每个项目都要回答“我为什么要点开这个仓库”以及“它可能帮我少踩什么坑”。
- 可信表达：允许保留不确定性。不要把仓库描述扩写成未经证实的能力，不要伪造 benchmark、公司案例或用户量。
- 叙述视角：只使用“本文”“该项目”“这个仓库”“这类工具”；不要使用“我们”“咱们”“小编”“笔者”等账号自称。
- 信息边界：正文不要贴 GitHub 地址。仓库名本身可以出现，URL 和“项目地址”段落不要出现。
""".strip()

    def _build_fallback_project_summary(self, ranking: WeeklyRankingRecord) -> str:
        """当模型没有给图片配套概要时，用周榜数据生成一条短说明。"""

        language_text = ranking.language or "未标注语言"
        return (
            f"这张图用于解释 {ranking.full_name} 的核心流程；"
            f"该项目本周增长 {ranking.star_growth} stars，主要技术栈为 {language_text}。"
        )

    def _remove_github_urls_and_link_sections(self, text: str) -> str:
        """移除正文里的 GitHub URL、项目地址段落和自动追加链接段，保证公众号正文不直接贴仓库地址。"""

        if not text:
            return ""

        normalized_text = text
        normalized_text = re.sub(
            r"\n?###\s*本期真实榜单链接[\s\S]*?(?=\n###\s+|\Z)",
            "",
            normalized_text,
            flags=re.IGNORECASE,
        )
        normalized_text = re.sub(
            r"\[([^\]]+)\]\(https?://(?:www\.)?github\.com/[^\s)]+\)",
            r"\1",
            normalized_text,
            flags=re.IGNORECASE,
        )
        normalized_text = re.sub(
            r"https?://(?:www\.)?github\.com/[^\s)]+",
            "",
            normalized_text,
            flags=re.IGNORECASE,
        )
        normalized_text = re.sub(
            r"(?m)^\s*(?:>?\s*)?(?:GitHub\s*)?(?:项目)?地址[:：].*$",
            "",
            normalized_text,
            flags=re.IGNORECASE,
        )
        normalized_text = re.sub(
            r"(?:GitHub\s*)?(?:项目)?地址[:：]\s*",
            "",
            normalized_text,
            flags=re.IGNORECASE,
        )
        normalized_text = re.sub(
            r"(?m)^\s*(?:>?\s*)?stars\s*数量最多项目地址[:：].*$",
            "",
            normalized_text,
            flags=re.IGNORECASE,
        )
        normalized_text = re.sub(r"[ \t]+\n", "\n", normalized_text)
        normalized_text = re.sub(r"\n{3,}", "\n\n", normalized_text)
        return normalized_text.strip()

    def _normalize_author_voice(self, text: str) -> str:
        """把模型容易写出的账号自称改成文章视角，降低 AI 营销口吻。"""

        if not text:
            return ""

        replacements = (
            ("让我们一起来看看", "下面直接看"),
            ("让我们深入了解", "下面展开看"),
            ("让我们看看", "下面看"),
            ("让我们", "不妨"),
            ("我们可以看到", "可以看到"),
            ("我们会发现", "会发现"),
            ("我们发现", "可以发现"),
            ("我们来", "下面"),
            ("我们不只", "本文不只"),
            ("我们的", "该项目的"),
            ("我们", "本文"),
            ("咱们", "本文"),
            ("小编", "本文"),
            ("笔者", "本文"),
        )
        normalized_text = text
        for old, new in replacements:
            normalized_text = normalized_text.replace(old, new)
        normalized_text = re.sub(r"\bI\b", "本文", normalized_text)
        normalized_text = re.sub(r"\bwe\b", "本文", normalized_text, flags=re.IGNORECASE)
        return normalized_text.strip()

    def _contains_github_url(self, text: str) -> bool:
        """检测正文是否仍然包含 GitHub URL。"""

        return bool(re.search(r"https?://(?:www\.)?github\.com/", text or "", flags=re.IGNORECASE))

    def _log_content_quality_warnings(
            self,
            title: str,
            digest: str,
            article_markdown: str,
            video_script: str,
            voiceover_text: str,
            project_count: int,
    ) -> None:
        """对模型文案做轻量质量自检；当前只记录日志，不阻断任务。"""

        warnings: list[str] = []
        opening_text = article_markdown[:260]
        compact_text = "\n".join([title, digest, opening_text, voiceover_text[:180]])
        for phrase in self._banned_ai_style_phrases:
            if phrase in compact_text:
                warnings.append(f"检测到疑似 AI 套话：{phrase}")

        if self._contains_github_url(article_markdown):
            warnings.append("正文仍包含 GitHub URL")
        if "项目地址" in article_markdown or "GitHub 地址" in article_markdown:
            warnings.append("正文仍包含项目地址提示")
        if "我们" in article_markdown or "咱们" in article_markdown:
            warnings.append("正文仍包含第一人称账号自称")

        project_section_title = f"### Top {project_count} 项目拆解"
        for section_title in ("### 本周主线", project_section_title, "### 工程启发"):
            if section_title not in article_markdown:
                warnings.append(f"文章缺少推荐结构标题：{section_title}")

        missing_timeline_markers = [marker for marker in self._timeline_markers(project_count) if marker not in video_script]
        if missing_timeline_markers:
            warnings.append("视频脚本缺少时间轴：" + ", ".join(missing_timeline_markers))

        if len(title) > 34:
            warnings.append(f"title 超过 34 字：length={len(title)}")
        if len(digest) > 110:
            warnings.append(f"digest 超过 110 字：length={len(digest)}")
        if len(article_markdown) > 1800:
            warnings.append(f"article_markdown 可能偏长：length={len(article_markdown)}")
        if len(video_script) > 1100:
            warnings.append(f"video_script 可能偏长：length={len(video_script)}")
        if len(voiceover_text) > 750:
            warnings.append(f"voiceover_text 可能偏长：length={len(voiceover_text)}")

        if not warnings:
            self.logger.info("SummaryTask 文案质量自检通过")
            return

        self.logger.warning("SummaryTask 文案质量自检提示：%s", "；".join(warnings))

    def _replace_repository_aliases(self, text: str, repository_aliases: dict[str, str]) -> str:
        """把模型输出中的仓库名别名或拼写错误替换成 GitHub 榜单真实 full_name。"""

        normalized_text = text
        for raw_repository_full_name, expected_repository_full_name in repository_aliases.items():
            if raw_repository_full_name == expected_repository_full_name:
                continue
            normalized_text = normalized_text.replace(raw_repository_full_name, expected_repository_full_name)
        return normalized_text

    def _detect_repository_aliases_from_texts(
            self,
            texts: list[str],
            expected_repository_names: list[str],
    ) -> dict[str, str]:
        """扫描模型文本里的 owner/repo 片段，找出疑似拼写错误并映射到真实榜单仓库名。"""

        aliases: dict[str, str] = {}
        expected_name_set = set(expected_repository_names)
        for text in texts:
            if not text:
                continue
            for match in self._repository_token_pattern.finditer(text):
                candidate = match.group(1).strip()
                if candidate in expected_name_set:
                    continue
                if self._looks_like_url_fragment(candidate):
                    continue
                expected_repository_name = self._best_repository_alias_match(
                    candidate=candidate,
                    expected_repository_names=expected_repository_names,
                )
                if expected_repository_name is None:
                    continue
                aliases[candidate] = expected_repository_name
                self.logger.warning(
                    "检测到疑似仓库名拼写错误，已映射为真实榜单仓库名：raw=%s expected=%s",
                    candidate,
                    expected_repository_name,
                )
        return aliases

    def _best_repository_alias_match(
            self,
            candidate: str,
            expected_repository_names: list[str],
    ) -> str | None:
        """从真实榜单仓库名中找出最像 candidate 的一项。"""

        candidate_owner, candidate_repo = self._split_repository_full_name(candidate)
        if not candidate_owner or not candidate_repo:
            return None

        best_match: str | None = None
        best_score = 0.0
        for expected_repository_name in expected_repository_names:
            expected_owner, expected_repo = self._split_repository_full_name(expected_repository_name)
            if not expected_owner or not expected_repo:
                continue

            full_score = SequenceMatcher(
                None,
                candidate.lower(),
                expected_repository_name.lower(),
            ).ratio()
            owner_score = SequenceMatcher(
                None,
                candidate_owner.lower(),
                expected_owner.lower(),
            ).ratio()
            repo_score = SequenceMatcher(
                None,
                candidate_repo.lower(),
                expected_repo.lower(),
            ).ratio()
            combined_score = max(full_score, owner_score * 0.45 + repo_score * 0.55)
            if combined_score > best_score:
                best_score = combined_score
                best_match = expected_repository_name

        if best_match is None:
            return None
        if best_score >= 0.88:
            return best_match
        return None

    def _split_repository_full_name(self, repository_full_name: str) -> tuple[str, str]:
        """把 owner/repo 拆成 owner 和 repo；格式不合法时返回空字符串。"""

        parts = repository_full_name.strip().split("/", 1)
        if len(parts) != 2:
            return "", ""
        return parts[0].strip(), parts[1].strip()

    def _looks_like_url_fragment(self, candidate: str) -> bool:
        """过滤 URL 中误扫出来的 github.com/owner 这类片段。"""

        owner, _ = self._split_repository_full_name(candidate)
        return owner.lower() in {"github.com", "www.github.com", "http", "https"}

    def _append_canonical_ranking_links(
            self,
            article_markdown: str,
            rankings: list[WeeklyRankingRecord],
    ) -> str:
        """把 SearchTask 的真实榜单链接追加到文章中，避免 LLM 拼错项目地址。"""

        section_title = "### 本期真实榜单链接"
        if section_title in article_markdown:
            return article_markdown

        lines = [section_title]
        for item in rankings:
            lines.append(
                f"{item.rank}. {item.full_name}：stars {item.current_stars}，"
                f"本周增长 {item.star_growth}，地址 {item.html_url}"
            )
        return f"{article_markdown}\n\n" + "\n".join(lines)
