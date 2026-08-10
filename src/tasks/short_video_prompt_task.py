from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from typing import Any

from src.providers.deepseek_provider import DeepSeekMessage, DeepSeekProvider, parse_json_object_from_text
from src.repositories.generated_content_repository import GeneratedContentForStoryboard, GeneratedContentRepository
from src.repositories.video_storyboard_repository import VideoStoryboardInput, VideoStoryboardRepository
from src.tasks.base_task import BaseTask
from src.tasks.task_context import TaskContext


class ShortVideoPromptTask(BaseTask):
    """生成短视频制作蓝图，让文本、图片和视频提示词层层递进。"""

    task_name = "ShortVideoPromptTask"
    opening_duration_seconds = 5
    closing_duration_seconds = 5
    target_duration_seconds = 60
    _repository_token_pattern = re.compile(r"(?<![\w.-])([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)(?![\w.-])")

    def execute(self, context: TaskContext) -> dict[str, Any]:
        """读取最新 Summary 内容，生成可审查的 Seedance 视频蓝图。"""

        content_repository = GeneratedContentRepository(database_manager=context.database_manager)
        storyboard_repository = VideoStoryboardRepository(database_manager=context.database_manager)
        content = content_repository.latest_for_storyboard_generation()
        if content is None:
            raise RuntimeError("没有可生成短视频蓝图的 generated_contents，请先运行 SummaryTask")
        project_count = len(content.image_prompts)
        if project_count < 1:
            raise RuntimeError(f"content_id={content.id} 至少需要 1 条 image_prompt")

        provider = DeepSeekProvider(config=context.config, run_name="wechat.video_storyboard.generate")
        raw_response_model = ""
        fallback_used = False
        try:
            response, normalized = self._generate_normalized_storyboard(
                provider=provider,
                content=content,
                video_instruction=context.config.runtime_prompt("video"),
            )
            raw_response_model = response.model
        except Exception as exc:
            fallback_used = True
            self.logger.warning(
                "DeepSeek 短视频口播生成失败，将使用本地结构化兜底：content_id=%s error=%s",
                content.id,
                exc,
            )
            normalized = self._build_fallback_storyboard(content)

        record = storyboard_repository.upsert(
            VideoStoryboardInput(
                content_id=content.id,
                title=normalized["video_title"],
                progressive_script=normalized["progressive_script"],
                seedance_prompt=normalized["seedance_master_prompt"],
                architecture_image_prompts=normalized["architecture_image_prompts"],
                storyboard=normalized,
                status="ready",
            )
        )

        self.logger.info(
            "短视频蓝图已生成：content_id=%s storyboard_id=%s scenes=%s fallback=%s",
            content.id,
            record.id,
            len(normalized["scenes"]),
            fallback_used,
        )
        return {
            "content_id": content.id,
            "storyboard_id": record.id,
            "video_title": record.title,
            "scene_count": len(normalized["scenes"]),
            "architecture_image_prompt_count": len(normalized["architecture_image_prompts"]),
            "progressive_script_length": len(record.progressive_script),
            "seedance_prompt_length": len(record.seedance_prompt),
            "model": raw_response_model or context.config.llm_model,
            "fallback_used": fallback_used,
            "skipped": False,
            "network_called": not fallback_used,
        }

    def _generate_normalized_storyboard(
        self,
        provider: DeepSeekProvider,
        content: GeneratedContentForStoryboard,
        video_instruction: str,
    ) -> tuple[Any, dict[str, Any]]:
        """调用 DeepSeek 生成短口播，再由代码确定性组装短视频蓝图。"""

        attempts = [
            self._build_script_messages(content, video_instruction),
            self._build_script_retry_messages(content, video_instruction),
        ]
        last_error: Exception | None = None
        for attempt_index, messages in enumerate(attempts, start=1):
            response = provider.chat(messages)
            try:
                parsed = parse_json_object_from_text(response.content)
                script_payload = self._normalize_script_payload(parsed=parsed, content=content)
                normalized = self._build_storyboard_from_script_payload(
                    content=content,
                    script_payload=script_payload,
                )
                if attempt_index > 1:
                    self.logger.info("DeepSeek 第 %s 次尝试输出了可解析短视频口播 JSON", attempt_index)
                return response, normalized
            except Exception as exc:
                last_error = exc
                if attempt_index < len(attempts):
                    self.logger.warning("DeepSeek 第 %s 次短视频口播 JSON 异常，将重试短版：%s", attempt_index, exc)
                    continue
                raise

        raise RuntimeError("DeepSeek 短视频蓝图生成失败") from last_error

    def _build_script_messages(
        self,
        content: GeneratedContentForStoryboard,
        video_instruction: str,
    ) -> list[DeepSeekMessage]:
        """构造只生成渐进式口播的小 JSON 请求。"""

        project_payload = [
            {
                "index": index,
                "repository_full_name": item["repository_full_name"],
            }
            for index, item in enumerate(content.image_prompts, start=1)
        ]
        project_count = len(project_payload)
        per_project_duration = self._project_scene_durations(project_count)
        system_prompt = (
            "你是一名技术科普短视频口播编导。"
            "你只负责把 GitHub 周榜改写成 60 秒口播文案，不负责输出复杂分镜。"
            "只输出合法 JSON 对象。"
        )
        user_prompt = f"""
请基于以下内容生成短视频口播 JSON。

字段必须包含：
video_title: 36字以内
opening_line: 80字以内
project_summaries: {project_count}项数组，每项包含 repository_full_name、spoken_text、architecture_focus
closing_line: 90字以内
progressive_script: 900字以内，按“本周 GitHub 热门项目来了，第一是...第二是...”逐个讲，逻辑递进

要求：
- 只输出 JSON，不要 Markdown。
- project_summaries 顺序必须与输入项目一致。
- 第 i 个 spoken_text 要适合约 {per_project_duration[0] if per_project_duration else 10} 秒口播；总时长目标为 60 秒。
- architecture_focus 要说明这张图重点表现什么机制，但不要要求图片里出现文字、仓库名、代码、数字、标题或 logo。
- 不要编造项目能力；不确定时用“从仓库描述看”。
{self._build_runtime_instruction_section("管理员视频策略", video_instruction)}

标题：{content.title}
摘要：{content.digest}
文章正文：
{content.article_markdown[:1800]}

项目：
{json.dumps(project_payload, ensure_ascii=False)}
"""
        return [
            DeepSeekMessage(role="system", content=system_prompt),
            DeepSeekMessage(role="user", content=user_prompt.strip()),
        ]

    def _build_script_retry_messages(
        self,
        content: GeneratedContentForStoryboard,
        video_instruction: str,
    ) -> list[DeepSeekMessage]:
        """构造更短的口播 JSON 重试请求。"""

        project_names = [item["repository_full_name"] for item in content.image_prompts]
        system_prompt = "你只输出合法 JSON 对象。不要 Markdown，不要解释。"
        user_prompt = f"""
生成 60 秒 GitHub 热门项目技术科普口播 JSON。
字段：video_title、opening_line、project_summaries、closing_line、progressive_script。
project_summaries 正好 {len(project_names)} 项，每项含 repository_full_name、spoken_text、architecture_focus。
项目顺序：{json.dumps(project_names, ensure_ascii=False)}
主题：{content.title}
摘要：{content.digest}
{self._build_runtime_instruction_section("管理员视频策略", video_instruction)}
"""
        return [
            DeepSeekMessage(role="system", content=system_prompt),
            DeepSeekMessage(role="user", content=user_prompt.strip()),
        ]

    def _normalize_script_payload(
        self,
        parsed: dict[str, Any],
        content: GeneratedContentForStoryboard,
    ) -> dict[str, Any]:
        """规范化模型产出的短口播 JSON。"""

        project_names = [str(item["repository_full_name"]) for item in content.image_prompts]
        raw_project_summaries = parsed.get("project_summaries", [])
        if not isinstance(raw_project_summaries, list):
            raw_project_summaries = []

        repository_aliases: dict[str, str] = {}
        project_summaries: list[dict[str, Any]] = []
        for index, project_name in enumerate(project_names, start=1):
            raw_item = self._find_item_by_repository(raw_project_summaries, project_name)
            if not raw_item:
                raw_item = self._item_by_index(raw_project_summaries, index - 1)

            raw_repository_name = str(raw_item.get("repository_full_name", "")).strip()
            if raw_repository_name and raw_repository_name != project_name:
                repository_aliases[raw_repository_name] = project_name
                self.logger.warning(
                    "DeepSeek 短视频口播仓库名与 Summary 不一致，已按项目顺序纠偏：index=%s raw=%s expected=%s",
                    index,
                    raw_repository_name,
                    project_name,
                )

            spoken_text = str(raw_item.get("spoken_text", "")).strip()
            if raw_repository_name:
                spoken_text = spoken_text.replace(raw_repository_name, project_name)
            if not spoken_text:
                spoken_text = f"第 {index} 个项目是 {project_name}，它代表了本周技术趋势里的一个关键方向。"
            architecture_focus = str(raw_item.get("architecture_focus", "")).strip()
            if raw_repository_name:
                architecture_focus = architecture_focus.replace(raw_repository_name, project_name)
            if not architecture_focus:
                architecture_focus = "展示输入、核心模块、处理流程、输出和适用场景。"
            project_summaries.append(
                {
                    "project_index": index,
                    "repository_full_name": project_name,
                    "spoken_text": spoken_text[:220],
                    "architecture_focus": architecture_focus[:220],
                }
            )

        video_title = str(parsed.get("video_title", content.title)).strip() or content.title
        opening_line = str(parsed.get("opening_line", "")).strip()
        closing_line = str(parsed.get("closing_line", "")).strip()
        progressive_script = str(parsed.get("progressive_script", "")).strip()
        repository_text_candidates = [video_title, opening_line, closing_line, progressive_script]
        for item in raw_project_summaries:
            if not isinstance(item, dict):
                continue
            repository_text_candidates.extend(
                [
                    str(item.get("repository_full_name", "")).strip(),
                    str(item.get("spoken_text", "")).strip(),
                    str(item.get("architecture_focus", "")).strip(),
                ]
            )
        repository_aliases.update(
            self._detect_repository_aliases_from_texts(
                texts=repository_text_candidates,
                expected_repository_names=project_names,
            )
        )

        project_summaries = [
            {
                **item,
                "spoken_text": self._replace_repository_aliases(str(item["spoken_text"]), repository_aliases),
                "architecture_focus": self._replace_repository_aliases(
                    str(item["architecture_focus"]),
                    repository_aliases,
                ),
            }
            for item in project_summaries
        ]

        video_title = self._replace_repository_aliases(video_title, repository_aliases)
        opening_line = self._replace_repository_aliases(opening_line, repository_aliases)
        project_count = len(project_summaries)
        if not opening_line:
            opening_line = f"本周 GitHub 热门项目 Top {project_count} 来了，我们不只看 star，也看这些项目背后的技术趋势。"
        closing_line = self._replace_repository_aliases(closing_line, repository_aliases)
        if not closing_line:
            closing_line = f"这 {project_count} 个项目放在一起看，说明 AI 工具正在从单点自动化走向更完整的工程工作流。"
        progressive_script = self._replace_repository_aliases(progressive_script, repository_aliases)
        if not progressive_script:
            progressive_script = opening_line + "".join(item["spoken_text"] for item in project_summaries) + closing_line

        return {
            "video_title": video_title,
            "opening_line": opening_line[:160],
            "project_summaries": project_summaries,
            "closing_line": closing_line[:180],
            "progressive_script": progressive_script[:900],
        }

    def _build_storyboard_from_script_payload(
        self,
        content: GeneratedContentForStoryboard,
        script_payload: dict[str, Any],
    ) -> dict[str, Any]:
        """根据短口播确定性生成开场、项目段与结尾分镜及 Seedance 主 prompt。"""

        project_summaries = script_payload["project_summaries"]
        architecture_prompts = [
            {
                "project_index": item["project_index"],
                "repository_full_name": item["repository_full_name"],
                "project_summary_text": item["spoken_text"],
                "architecture_prompt": self._normalize_architecture_prompt(
                    repository_full_name=item["repository_full_name"],
                    prompt=(
                        "生成一张单项目技术教学架构图。"
                        f"重点表现：{item['architecture_focus']} "
                        "画面结构为左侧输入节点、中心核心模块、右侧输出节点、底部场景节点，节点与箭头关系清晰。"
                    ),
                ),
            }
            for item in project_summaries
        ]

        project_count = len(project_summaries)
        scene_durations = self._scene_durations(project_count)
        scenes: list[dict[str, Any]] = [
            {
                "scene_index": 1,
                "time_range": self._time_range_for_scene(1, scene_durations),
                "duration_seconds": scene_durations[0],
                "purpose": "开场，本周趋势总览",
                "repository_full_name": None,
                "narration": script_payload["opening_line"],
                "subtitle": f"本周 GitHub Top {project_count} 技术趋势",
                "visual_design": f"{project_count} 个项目卡片围绕 GitHub 周榜中心形成清晰的教学总览图，白色纸张背景、深蓝标题与蓝色流程箭头。",
                "motion_design": f"镜头从总览卡片平滑推进，{project_count} 个项目节点按顺序高亮。",
                "transition_to_next": "中心雷达节点拉近，切入项目 1 的架构图。",
                "seedance_scene_prompt": f"开场趋势总览，{project_count} 个项目节点形成知识雷达，镜头缓慢推进，科技教学风。",
            }
        ]
        for item in project_summaries:
            scene_index = int(item["project_index"]) + 1
            scenes.append(
                {
                    "scene_index": scene_index,
                    "time_range": self._time_range_for_scene(scene_index, scene_durations),
                    "duration_seconds": scene_durations[scene_index - 1],
                    "purpose": self._project_scene_purpose(
                        project_index=int(item["project_index"]),
                        project_count=project_count,
                    ),
                    "repository_full_name": item["repository_full_name"],
                    "narration": item["spoken_text"],
                    "subtitle": item["repository_full_name"],
                    "visual_design": (
                        f"{item['repository_full_name']} 的技术架构图，占据画面中心；"
                        "输入、核心模块、流程箭头、输出依次排列，像高质量技术 PPT。"
                    ),
                    "motion_design": self._project_motion_design(
                        project_index=int(item["project_index"]),
                        project_count=project_count,
                    ),
                    "transition_to_next": "用发光流程线横向滑动，衔接下一个项目。",
                    "seedance_scene_prompt": (
                        f"{item['repository_full_name']} 技术架构讲解，{item['architecture_focus']} "
                        f"{self._project_motion_design(project_index=int(item['project_index']), project_count=project_count)}"
                    ),
                }
            )
        scenes.append(
            {
                "scene_index": project_count + 2,
                "time_range": self._time_range_for_scene(project_count + 2, scene_durations),
                "duration_seconds": scene_durations[-1],
                "purpose": "结尾 CTA",
                "repository_full_name": None,
                "narration": script_payload["closing_line"],
                "subtitle": "关注本周开源技术趋势",
                "visual_design": f"{project_count} 张架构图缩略卡汇聚成一张总趋势图，中心是 AI Agent、代码理解、工程教育三个关键词。",
                "motion_design": f"{project_count} 个项目卡片向中心聚合，最后定格在趋势总结卡片。",
                "transition_to_next": "视频结束。",
                "seedance_scene_prompt": f"结尾总结，{project_count} 个项目卡片汇聚成技术趋势图，干净收束。",
            }
        )

        seedance_prompt = self._build_seedance_prompt_from_scenes(
            title=script_payload["video_title"],
            progressive_script=script_payload["progressive_script"],
            scenes=scenes,
        )
        return {
            "video_title": script_payload["video_title"],
            "total_duration_seconds": sum(scene_durations),
            "progressive_script": script_payload["progressive_script"],
            "architecture_image_prompts": architecture_prompts,
            "scenes": scenes,
            "seedance_master_prompt": seedance_prompt,
            "quality_constraints": [
                "视频是一个连续教学讲解，不是多张图硬切。",
                "每段画面必须跟随对应项目的旁白逐步展开。",
                "少用随机抽象画面，多用流程图、架构图、代码卡片、模块高亮。",
                "不要生成乱码文字、错误 UI 或无意义代码。",
            ],
            "source": {
                "content_id": content.id,
                "week_end": content.week_end,
                "summary_title": content.title,
            },
        }

    def _build_fallback_storyboard(self, content: GeneratedContentForStoryboard) -> dict[str, Any]:
        """DeepSeek 不稳定时生成一份确定性的基础视频蓝图。"""

        project_names = [str(item["repository_full_name"]) for item in content.image_prompts]
        project_lines = [
            f"第 {index} 个项目是 {project_name}，它代表了本周 GitHub 技术趋势里的一个关键方向。"
            for index, project_name in enumerate(project_names, start=1)
        ]
        project_count = len(project_names)
        progressive_script = (
            f"本周 GitHub 热门项目 Top {project_count} 来了。我们不只看 star 数，而是看这些项目背后的技术趋势。"
            + "".join(project_lines)
            + f"把这 {project_count} 个项目放在一起看，你会发现开发者工具正在从单点自动化，走向更完整的知识组织、代码理解和工程学习工作流。"
        )
        architecture_prompts = [
            {
                "project_index": index,
                "repository_full_name": project_name,
                "project_summary_text": f"{project_name} 的核心价值和工程使用场景。",
                "architecture_prompt": self._normalize_architecture_prompt(
                    repository_full_name=project_name,
                    prompt=(
                        "生成一张科技教学风架构图："
                        "白色或浅灰纸张背景、深蓝标题色块、蓝色流程箭头，展示输入、核心模块、处理流程、输出和适用场景，"
                        "像高质量技术课件，构图清晰；只允许极少量中文短标签，不要出现仓库名、英文、数字、代码、logo 或水印。"
                    ),
                ),
            }
            for index, project_name in enumerate(project_names, start=1)
        ]
        scenes = []
        scene_durations = self._scene_durations(project_count)
        for index, duration in enumerate(scene_durations, start=1):
            repository_name = self._scene_repository_name(index, content, project_count)
            is_opening = index == 1
            is_closing = index == len(scene_durations)
            purpose = (
                "开场趋势总览"
                if is_opening
                else "结尾 CTA"
                if is_closing
                else self._project_scene_purpose(index - 1, project_count)
            )
            narration = (
                "本周 GitHub 热榜的关键词，是 AI Agent、代码理解和工程教育。"
                if is_opening
                else f"这 {project_count} 个项目共同说明：真正有价值的 AI 工具，必须把自动化、可解释性和工程实践连接起来。"
                if is_closing
                else f"第 {index - 1} 个项目是 {repository_name}，我们用一张架构图看清它解决的问题、核心模块和使用场景。"
            )
            scenes.append(
                {
                    "scene_index": index,
                    "time_range": self._time_range_for_scene(index, scene_durations),
                    "duration_seconds": duration,
                    "purpose": purpose,
                    "repository_full_name": repository_name,
                    "narration": narration,
                    "subtitle": purpose,
                    "visual_design": "白色纸张质感背景、深蓝标题色块、蓝色流程箭头的信息图，技术教学 PPT 动画式构图。",
                    "motion_design": "镜头缓慢推进，节点依次点亮，流程线平滑移动。",
                    "transition_to_next": "用发光连线和轻微推镜衔接下一段。",
                    "seedance_scene_prompt": f"{purpose}，画面与旁白同步推进，信息层级清晰。",
                }
            )

        seedance_prompt = self._build_seedance_prompt_from_scenes(
            title=content.title,
            progressive_script=progressive_script,
            scenes=scenes,
        )
        return {
            "video_title": content.title,
            "total_duration_seconds": sum(scene_durations),
            "progressive_script": progressive_script,
            "architecture_image_prompts": architecture_prompts,
            "scenes": scenes,
            "seedance_master_prompt": seedance_prompt,
            "quality_constraints": [
                "画面必须跟随旁白逐步展开，不要随机跳转主题。",
                "不要生成乱码文字、错误 UI 或无意义代码。",
                "整体像科技教学 PPT 动画，而不是图片硬切 slideshow。",
            ],
            "source": {
                "content_id": content.id,
                "week_end": content.week_end,
                "summary_title": content.title,
            },
        }

    def _build_seedance_prompt_from_scenes(
        self,
        title: str,
        progressive_script: str,
        scenes: list[dict[str, Any]],
    ) -> str:
        """从分镜拼出 Seedance 主 prompt。"""

        scene_text = "\n".join(
            f"{scene['time_range']}：{scene['purpose']}。画面：{scene['visual_design']} 动作：{scene['motion_design']} 旁白：{scene['narration']}"
            for scene in scenes
        )
        return (
            "生成一段 60 秒科技教学短视频，风格类似高质量技术科普栏目。"
            "白色或浅灰纸张质感背景，深蓝标题胶囊、蓝色流程箭头、清楚的中文短标签，PPT 动画式信息图，镜头平滑推进。"
            "视频必须按照时间顺序层层递进，旁白与画面同步，不要变成多张图片硬切。"
            f"标题：{title}。"
            f"完整口播：{progressive_script}"
            f"分镜结构：\n{scene_text}"
        )[:1400]

    def _normalize_architecture_prompt(self, repository_full_name: str, prompt: str) -> str:
        """补齐架构图 prompt 的强约束。"""

        base_prompt = prompt or "生成技术架构概览图。"
        base_prompt = base_prompt.replace(repository_full_name, "这个项目")
        constraints = (
            "统一科技教学风，白色或浅灰背景、深蓝标题块、蓝色流程箭头，清晰节点流程图；"
            "主体最多 6 个节点，最多 2 条主箭头，中心核心模块清楚，四周留白；"
            "只允许少量中文短标签表达节点用途；不要生成英文、字母、数字、代码、仓库名、logo、水印；"
            "允许圆角矩形、简洁图标、色块和细线箭头；"
            "不要仓库截图，不要 UI 截图，不要复杂网络乱线，不要齿轮、灯泡、握手、拼图等陈词滥调。"
        )
        return f"{base_prompt} {constraints}"[:650]

    def _find_item_by_repository(self, items: list[Any], repository_full_name: str) -> dict[str, Any]:
        """按 repository_full_name 在模型输出数组里找项目项。"""

        for item in items:
            if not isinstance(item, dict):
                continue
            if str(item.get("repository_full_name", "")).strip() == repository_full_name:
                return item
        return {}

    def _item_by_index(self, items: list[Any], index: int) -> dict[str, Any]:
        """模型没有严格返回仓库名时，按项目顺序取同位置口播项作为兜底。"""

        if index < 0 or index >= len(items):
            return {}
        item = items[index]
        if not isinstance(item, dict):
            return {}
        return item

    def _replace_repository_aliases(self, text: str, repository_aliases: dict[str, str]) -> str:
        """把口播 JSON 中的仓库名别名替换成 Summary 已校验过的真实 full_name。"""

        normalized_text = text
        for raw_repository_name, expected_repository_name in repository_aliases.items():
            if raw_repository_name == expected_repository_name:
                continue
            normalized_text = normalized_text.replace(raw_repository_name, expected_repository_name)
        return normalized_text

    def _detect_repository_aliases_from_texts(
        self,
        texts: list[str],
        expected_repository_names: list[str],
    ) -> dict[str, str]:
        """扫描口播文本里的 owner/repo 片段，把疑似拼写错误映射到真实项目名。"""

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
                    "检测到短视频口播疑似仓库名拼写错误，已映射为真实项目名：raw=%s expected=%s",
                    candidate,
                    expected_repository_name,
                )
        return aliases

    def _best_repository_alias_match(
        self,
        candidate: str,
        expected_repository_names: list[str],
    ) -> str | None:
        """从当前候选项目里找出和 candidate 最相似的仓库名。"""

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

    def _build_runtime_instruction_section(self, title: str, instruction: str) -> str:
        """把管理员配置以清晰边界追加到系统分镜约束中。"""

        normalized = instruction.strip()
        if not normalized:
            return ""
        return f"\n{title}（必须遵守，不能覆盖 JSON 输出格式和事实约束）：\n{normalized[:4000]}\n"

    def _scene_durations(self, project_count: int) -> list[int]:
        """在 60 秒总时长内为开场、项目和结尾确定性分配镜头时长。"""

        if project_count < 1:
            raise ValueError("project_count 必须大于 0")
        available = self.target_duration_seconds - self.opening_duration_seconds - self.closing_duration_seconds
        base_duration, remainder = divmod(available, project_count)
        project_durations = [base_duration + (1 if index < remainder else 0) for index in range(project_count)]
        return [self.opening_duration_seconds, *project_durations, self.closing_duration_seconds]

    def _project_scene_durations(self, project_count: int) -> list[int]:
        """返回项目讲解镜头时长，供口播 prompt 限制使用。"""

        return self._scene_durations(project_count)[1:-1]

    def _scene_repository_name(
        self,
        scene_index: int,
        content: GeneratedContentForStoryboard,
        project_count: int,
    ) -> str | None:
        """返回项目分镜对应的仓库名；开场和结尾为空。"""

        if scene_index in {1, project_count + 2}:
            return None
        project_index = scene_index - 2
        if project_index < 0 or project_index >= len(content.image_prompts):
            return None
        return str(content.image_prompts[project_index]["repository_full_name"])

    def _time_range_for_scene(self, scene_index: int, scene_durations: list[int]) -> str:
        """根据本轮动态时长返回分镜时间范围。"""

        start = sum(scene_durations[: scene_index - 1])
        end = start + scene_durations[scene_index - 1]
        return f"{start}-{end}s"

    def _project_scene_purpose(self, project_index: int, project_count: int) -> str:
        """返回项目分镜的渐进叙事目的，不再把节奏写死为五个项目。"""

        if project_index == 1:
            return "项目 1，架构图从中心展开"
        if project_index == project_count:
            return f"项目 {project_index}，总结工程启发"
        patterns = ["模块节点依次高亮", "流程线动画推进", "代码与工具链穿插", "关键能力逐层收束"]
        return f"项目 {project_index}，{patterns[(project_index - 2) % len(patterns)]}"

    def _project_motion_design(self, project_index: int, project_count: int) -> str:
        """返回与项目序号关联的教学式动态说明。"""

        if project_index == 1:
            return "架构图从中心节点向外展开，输入、核心模块、输出三层依次出现。"
        if project_index == project_count:
            return "项目卡片与工程启发标签并列出现，最后收束为一个趋势判断。"
        motion_designs = [
            "模块节点按旁白顺序依次高亮，关键路径用蓝色流程箭头连接。",
            "流程线从左到右推进，数据输入、处理、结果输出像电路一样点亮。",
            "代码卡片、命令行窗口和工具链节点穿插出现，但不展示可读乱码代码。",
            "镜头平滑推进，节点随旁白依次点亮并聚焦关键输入输出。",
        ]
        return motion_designs[(project_index - 2) % len(motion_designs)]
