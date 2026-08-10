from __future__ import annotations

from typing import Any

from src.repositories.generated_content_repository import GeneratedContentRepository
from src.repositories.media_asset_repository import MediaAssetRecord, MediaAssetRepository
from src.repositories.video_clip_plan_repository import VideoClipPlanInput, VideoClipPlanRepository
from src.repositories.video_storyboard_repository import VideoStoryboardRecord, VideoStoryboardRepository
from src.tasks.base_task import BaseTask
from src.tasks.task_context import TaskContext


class VideoClipPlanTask(BaseTask):
    """把完整短视频蓝图拆成多段 15 秒 clip 生产计划。"""

    task_name = "VideoClipPlanTask"

    def execute(self, context: TaskContext) -> dict[str, Any]:
        """读取最新 storyboard 和架构图素材，生成 7 段 15 秒视频计划。"""

        content_repository = GeneratedContentRepository(database_manager=context.database_manager)
        storyboard_repository = VideoStoryboardRepository(database_manager=context.database_manager)
        media_asset_repository = MediaAssetRepository(database_manager=context.database_manager)
        clip_plan_repository = VideoClipPlanRepository(database_manager=context.database_manager)

        content = content_repository.latest_for_video_generation()
        if content is None:
            raise RuntimeError("没有可生成视频 clip 计划的 generated_contents，请先运行 SummaryTask")

        storyboard = storyboard_repository.latest_for_content(content.id)
        if storyboard is None:
            raise RuntimeError(f"content_id={content.id} 没有 video_storyboards，请先运行 ShortVideoPromptTask")

        scenes = self._storyboard_scenes(storyboard)
        if len(scenes) != 7:
            raise RuntimeError(f"storyboard_id={storyboard.id} scenes 必须正好 7 段，当前 {len(scenes)} 段")

        image_assets = media_asset_repository.list_by_content_id(content.id, "image")
        image_assets_by_repository = self._active_image_assets_by_repository(image_assets)
        all_reference_image_asset_ids = [asset.id for asset in self._sort_image_assets(image_assets)]

        clip_plans = self._build_clip_plans(
            context=context,
            storyboard=storyboard,
            scenes=scenes,
            image_assets_by_repository=image_assets_by_repository,
            all_reference_image_asset_ids=all_reference_image_asset_ids,
        )
        records = clip_plan_repository.replace_for_storyboard(
            storyboard_id=storyboard.id,
            clip_plans=clip_plans,
        )

        total_duration_seconds = sum(record.planned_duration_seconds for record in records)
        self.logger.info(
            "视频 clip 计划已生成：content_id=%s storyboard_id=%s clips=%s total_duration=%ss",
            content.id,
            storyboard.id,
            len(records),
            total_duration_seconds,
        )
        return {
            "content_id": content.id,
            "storyboard_id": storyboard.id,
            "clip_count": len(records),
            "planned_clip_duration_seconds": [record.planned_duration_seconds for record in records],
            "total_duration_seconds": total_duration_seconds,
            "provider": context.config.video_provider,
            "model": context.config.video_model,
            "reference_image_asset_count": len(all_reference_image_asset_ids),
            "clip_plan_ids": [record.id for record in records],
            "skipped": False,
            "network_called": False,
        }

    def _storyboard_scenes(self, storyboard: VideoStoryboardRecord) -> list[dict[str, Any]]:
        """从 storyboard_json 中读取 7 段分镜。"""

        raw_scenes = storyboard.storyboard.get("scenes", [])
        if not isinstance(raw_scenes, list):
            return []
        return [scene for scene in raw_scenes if isinstance(scene, dict)]

    def _build_clip_plans(
        self,
        context: TaskContext,
        storyboard: VideoStoryboardRecord,
        scenes: list[dict[str, Any]],
        image_assets_by_repository: dict[str, MediaAssetRecord],
        all_reference_image_asset_ids: list[int],
    ) -> list[VideoClipPlanInput]:
        """根据分镜语义时长生成连续的视觉 clip 计划。"""

        clip_plans: list[VideoClipPlanInput] = []
        output_start_second = 0
        for clip_index, scene in enumerate(scenes, start=1):
            clip_duration = self._scene_duration_seconds(context=context, scene=scene)
            start_second = output_start_second
            end_second = start_second + clip_duration
            repository_full_name = self._scene_repository_full_name(scene)
            reference_image_asset_ids = []
            if context.config.video_reference_images_enabled:
                reference_image_asset_ids = self._reference_image_asset_ids_for_scene(
                    repository_full_name=repository_full_name,
                    image_assets_by_repository=image_assets_by_repository,
                    all_reference_image_asset_ids=all_reference_image_asset_ids,
                    clip_index=clip_index,
                    total_clip_count=len(scenes),
                )
            narration = str(scene.get("narration", "")).strip()
            subtitle = str(scene.get("subtitle", "")).strip()
            visual_design = str(scene.get("visual_design", "")).strip()
            motion_design = str(scene.get("motion_design", "")).strip()
            transition_to_next = str(scene.get("transition_to_next", "")).strip()
            purpose = str(scene.get("purpose", "")).strip() or f"第 {clip_index} 段"

            clip_plans.append(
                VideoClipPlanInput(
                    content_id=storyboard.content_id,
                    storyboard_id=storyboard.id,
                    clip_index=clip_index,
                    source_scene_index=int(scene.get("scene_index", clip_index) or clip_index),
                    clip_title=f"{clip_index:02d}. {purpose}",
                    repository_full_name=repository_full_name,
                    planned_duration_seconds=clip_duration,
                    output_start_second=start_second,
                    output_end_second=end_second,
                    narration=narration,
                    subtitle=subtitle,
                    visual_design=visual_design,
                    motion_design=motion_design,
                    transition_to_next=transition_to_next,
                    seedance_prompt=self._build_clip_seedance_prompt(
                        context=context,
                        storyboard=storyboard,
                        clip_index=clip_index,
                        total_clip_count=len(scenes),
                        purpose=purpose,
                        narration=narration,
                        subtitle=subtitle,
                        visual_design=visual_design,
                        motion_design=motion_design,
                        transition_to_next=transition_to_next,
                        repository_full_name=repository_full_name,
                        clip_duration=clip_duration,
                    ),
                    reference_image_asset_ids=reference_image_asset_ids,
                    provider=context.config.video_provider,
                    status="planned",
                    metadata={
                        "plan_strategy": "semantic_duration_visual_clips",
                        "plan_strategy_version": "video_first_spec_bound_v1",
                        "expected_total_duration_seconds": storyboard.storyboard.get(
                            "total_duration_seconds", context.config.video_duration_seconds
                        ),
                        "requires_post_assembly": True,
                        "requires_post_video_narration": True,
                        "reference_images_enabled": context.config.video_reference_images_enabled,
                        "generate_audio_in_clip": context.config.video_generate_audio,
                        "watermark": context.config.video_watermark,
                        "resolution": context.config.video_resolution,
                        "aspect_ratio": context.config.video_aspect_ratio,
                        "model": context.config.video_model,
                    },
                )
            )
            output_start_second = end_second

        return clip_plans

    def _scene_duration_seconds(self, context: TaskContext, scene: dict[str, Any]) -> int:
        """以分镜的语义时长驱动请求，避免把所有片段拉成 15 秒。"""

        raw_duration = scene.get("duration_seconds", context.config.video_clip_duration_seconds)
        try:
            requested_duration = int(raw_duration)
        except (TypeError, ValueError):
            requested_duration = context.config.video_clip_duration_seconds
        return max(
            context.config.video_min_clip_duration_seconds,
            min(context.config.video_max_clip_duration_seconds, requested_duration),
        )

    def _build_clip_seedance_prompt(
        self,
        context: TaskContext,
        storyboard: VideoStoryboardRecord,
        clip_index: int,
        total_clip_count: int,
        purpose: str,
        narration: str,
        subtitle: str,
        visual_design: str,
        motion_design: str,
        transition_to_next: str,
        repository_full_name: str | None,
        clip_duration: int,
    ) -> str:
        """生成单段 Seedance prompt，保持所有 clip 风格统一。"""

        repository_hint = ""
        if repository_full_name:
            repository_hint = f"本段聚焦 GitHub 项目 {repository_full_name}。"

        prompt_parts = [
            f"生成一段 {clip_duration} 秒科技教学动态画面，这是完整长视频的第 {clip_index}/{total_clip_count} 段。",
            f"完整视频标题：{storyboard.title}。",
            context.config.video_clip_prompt_visual_system,
            context.config.video_clip_prompt_continuity_rule,
            repository_hint,
            f"本段目的：{purpose}。",
            f"本段讲解意图：{narration}。",
            f"画面设计：{visual_design}。",
            f"运动设计：{motion_design}。",
            f"结尾衔接：{transition_to_next}。",
            context.config.video_clip_prompt_motion_rule,
            context.config.video_clip_prompt_audio_rule,
            context.config.video_clip_prompt_negative_prompt,
        ]
        if context.config.video_reference_images_enabled:
            prompt_parts.append(context.config.video_clip_prompt_reference_image_rule)
        prompt = "".join(part for part in prompt_parts if str(part).strip())
        return prompt[: context.config.video_clip_prompt_max_length]

    def _reference_image_asset_ids_for_scene(
        self,
        repository_full_name: str | None,
        image_assets_by_repository: dict[str, MediaAssetRecord],
        all_reference_image_asset_ids: list[int],
        clip_index: int,
        total_clip_count: int,
    ) -> list[int]:
        """为每段 clip 选择参考图：项目段用单项目图，开场/结尾用全部图。"""

        if clip_index in {1, total_clip_count}:
            return all_reference_image_asset_ids
        if repository_full_name and repository_full_name in image_assets_by_repository:
            return [image_assets_by_repository[repository_full_name].id]
        return all_reference_image_asset_ids[:1]

    def _active_image_assets_by_repository(self, image_assets: list[MediaAssetRecord]) -> dict[str, MediaAssetRecord]:
        """按仓库名索引当前可用架构图素材。"""

        indexed_assets: dict[str, MediaAssetRecord] = {}
        for asset in self._sort_image_assets(image_assets):
            repository_full_name = str(asset.metadata.get("repository_full_name", "")).strip()
            if not repository_full_name:
                continue
            if repository_full_name in indexed_assets:
                continue
            indexed_assets[repository_full_name] = asset
        return indexed_assets

    def _sort_image_assets(self, image_assets: list[MediaAssetRecord]) -> list[MediaAssetRecord]:
        """按 prompt_index 和 asset_id 排序，保证参考图顺序稳定。"""

        return sorted(
            image_assets,
            key=lambda asset: (
                int(asset.metadata.get("prompt_index", 9999) or 9999),
                asset.id,
            ),
        )

    def _scene_repository_full_name(self, scene: dict[str, Any]) -> str | None:
        """读取分镜对应仓库名；开场和结尾为空。"""

        raw_value = scene.get("repository_full_name")
        if raw_value is None:
            return None
        normalized = str(raw_value).strip()
        return normalized or None
