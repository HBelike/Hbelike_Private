from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from src.config.config_manager import AppConfig
from src.database.database_manager import DatabaseManager
from src.repositories.article_layout_repository import ArticleLayoutRepository
from src.repositories.content_approval_repository import ContentApprovalRepository
from src.repositories.generated_content_repository import GeneratedContentRepository
from src.repositories.media_asset_repository import MediaAssetRecord, MediaAssetRepository
from src.services.article_layout_service import ArticleLayoutService, LOCAL_WECHAT_IMAGE_SCHEME
from src.services.wechat_title_service import compact_wechat_title
from src.repositories.video_clip_plan_repository import VideoClipPlanRepository
from src.repositories.video_storyboard_repository import VideoStoryboardRepository


class MediaPreviewError(RuntimeError):
    """媒体预览解析失败。"""


class MediaPreviewService:
    """负责构建审核预览数据，并安全解析本地媒体文件。"""

    def __init__(self, config: AppConfig, database_manager: DatabaseManager) -> None:
        self.config = config
        self.database_manager = database_manager

    def build_latest_preview(self) -> dict[str, Any]:
        """构建最新内容的审核预览数据。"""
        content_repository = GeneratedContentRepository(database_manager=self.database_manager)
        media_asset_repository = MediaAssetRepository(database_manager=self.database_manager)
        approval_repository = ContentApprovalRepository(database_manager=self.database_manager)
        article_layout_repository = ArticleLayoutRepository(database_manager=self.database_manager)
        storyboard_repository = VideoStoryboardRepository(database_manager=self.database_manager)
        clip_plan_repository = VideoClipPlanRepository(database_manager=self.database_manager)

        content = content_repository.latest_for_preview()
        if content is None:
            return {
                "content": None,
                "media_assets": [],
            }

        media_assets = media_asset_repository.list_for_content(content.id)
        latest_approval = approval_repository.latest_for_content(content.id)
        article_layout = article_layout_repository.get_by_content_id(content.id)
        preview_layout = None
        if article_layout is None:
            preview_layout = self._build_transient_article_layout(content=content, media_assets=media_assets)
        storyboard = storyboard_repository.latest_for_content(content.id)
        clip_plans = clip_plan_repository.list_by_content_id(content.id)
        return {
            "content": {
                "id": content.id,
                "week_end": content.week_end,
                "title": content.title,
                "wechat_title": compact_wechat_title(content.title),
                "digest": content.digest,
                "article_markdown": content.article_markdown,
                "video_script": content.video_script,
                "voiceover_text": content.voiceover_text,
                "image_prompts": content.image_prompts,
                "status": content.status,
                "created_at": content.created_at,
                "updated_at": content.updated_at,
            },
            "article_layout": preview_layout
            if article_layout is None
            else {
                "id": article_layout.id,
                "content_id": article_layout.content_id,
                "title": article_layout.title,
                "wechat_title": compact_wechat_title(article_layout.title),
                "digest": article_layout.digest,
                # 已落库的正式排版同样含有仅供 DeliverTask 处理的占位符；
                # 返回给浏览器前转换，但绝不回写数据库或影响公众号发布链路。
                "article_html": self._replace_wechat_image_placeholders(article_layout.article_html),
                "cover_asset_id": article_layout.cover_asset_id,
                "payload": article_layout.payload,
                "status": article_layout.status,
                "created_at": article_layout.created_at,
                "updated_at": article_layout.updated_at,
            },
            "video_storyboard": None
            if storyboard is None
            else {
                "id": storyboard.id,
                "content_id": storyboard.content_id,
                "title": storyboard.title,
                "progressive_script": storyboard.progressive_script,
                "seedance_prompt": storyboard.seedance_prompt,
                "architecture_image_prompts": storyboard.architecture_image_prompts,
                "storyboard": storyboard.storyboard,
                "status": storyboard.status,
                "created_at": storyboard.created_at,
                "updated_at": storyboard.updated_at,
            },
            "video_clip_plans": [
                {
                    "id": clip_plan.id,
                    "content_id": clip_plan.content_id,
                    "storyboard_id": clip_plan.storyboard_id,
                    "clip_index": clip_plan.clip_index,
                    "source_scene_index": clip_plan.source_scene_index,
                    "clip_title": clip_plan.clip_title,
                    "repository_full_name": clip_plan.repository_full_name,
                    "planned_duration_seconds": clip_plan.planned_duration_seconds,
                    "output_start_second": clip_plan.output_start_second,
                    "output_end_second": clip_plan.output_end_second,
                    "narration": clip_plan.narration,
                    "subtitle": clip_plan.subtitle,
                    "visual_design": clip_plan.visual_design,
                    "motion_design": clip_plan.motion_design,
                    "transition_to_next": clip_plan.transition_to_next,
                    "seedance_prompt": clip_plan.seedance_prompt,
                    "reference_image_asset_ids": clip_plan.reference_image_asset_ids,
                    "provider": clip_plan.provider,
                    "status": clip_plan.status,
                    "metadata": clip_plan.metadata,
                    "created_at": clip_plan.created_at,
                    "updated_at": clip_plan.updated_at,
                }
                for clip_plan in clip_plans
            ],
            "approval": None
            if latest_approval is None
            else {
                "id": latest_approval.id,
                "content_id": latest_approval.content_id,
                "decision": latest_approval.decision,
                "operator": latest_approval.operator,
                "comment": latest_approval.comment,
                "created_at": latest_approval.created_at,
            },
            "media_assets": [self._asset_to_preview_payload(asset) for asset in media_assets],
        }

    def _build_transient_article_layout(
        self,
        content: Any,
        media_assets: list[MediaAssetRecord],
    ) -> dict[str, Any] | None:
        """为审核台生成不落库的只读排版预览。

        ArticleLayoutTask 只处理审核通过后的正式排版稿；但审核台需要在通过前看到图文
        一对一的版式效果。这里复用同一个 ArticleLayoutService，只返回临时 payload，
        不修改 generated_contents、article_layouts 或 media_assets。
        """
        try:
            layout_service = ArticleLayoutService()
            build_result = layout_service.build(content=content, media_assets=media_assets)
            payload = layout_service.build_payload(
                content=content,
                result=build_result,
                media_assets=media_assets,
            )
        except (TypeError, ValueError, RuntimeError):
            return None

        return {
            "id": None,
            "content_id": content.id,
            "title": content.title,
            "wechat_title": compact_wechat_title(content.title),
            "digest": content.digest,
            # 正式公众号排版保留 wechat-image-asset:// 占位符，后续由 DeliverTask 上传并替换；
            # 审核台则必须使用本地媒体接口，避免浏览器尝试加载一个非 HTTP 协议而显示破图。
            "article_html": self._replace_wechat_image_placeholders(build_result.article_html),
            "cover_asset_id": build_result.cover_asset_id,
            "payload": {
                **payload,
                "preview_only": True,
            },
            "status": "preview",
            "created_at": content.created_at,
            "updated_at": content.updated_at,
        }

    def _replace_wechat_image_placeholders(self, article_html: str) -> str:
        """把仅供正式发布阶段使用的图片占位符替换为审核台可访问的本地 URL。"""

        prefix = self.config.preview_media_route_prefix.rstrip("/")
        pattern = re.compile(
            rf'(?P<attribute>src=["\']){re.escape(LOCAL_WECHAT_IMAGE_SCHEME)}://(?P<asset_id>\d+)(?P<quote>["\'])'
        )

        def replace(match: re.Match[str]) -> str:
            return f'{match.group("attribute")}{prefix}/{match.group("asset_id")}/file{match.group("quote")}'

        return pattern.sub(replace, article_html)

    def resolve_local_media_path(self, asset_id: int) -> tuple[Path, str | None]:
        """安全解析本地媒体路径，供 Web API 返回文件。"""
        media_asset_repository = MediaAssetRepository(database_manager=self.database_manager)
        asset = media_asset_repository.get_by_id(asset_id)
        if asset is None:
            raise MediaPreviewError(f"媒体资产不存在：asset_id={asset_id}")

        resolved_path = self._safe_resolve_local_path(asset)
        if not resolved_path.exists():
            raise MediaPreviewError(f"媒体文件不存在：asset_id={asset_id}")
        if not resolved_path.is_file():
            raise MediaPreviewError(f"媒体路径不是文件：asset_id={asset_id}")
        return resolved_path, asset.mime_type

    def _asset_to_preview_payload(self, asset: MediaAssetRecord) -> dict[str, Any]:
        """把媒体资产转换为不会泄露本地绝对路径的预览数据。"""
        remote_url = self._public_remote_url(asset)
        local_url = self._local_preview_url(asset)
        return {
            "id": asset.id,
            "content_id": asset.content_id,
            "asset_type": asset.asset_type,
            "provider": asset.provider,
            "mime_type": asset.mime_type,
            "status": asset.status,
            "remote_url": remote_url,
            "local_url": local_url,
            "preview_url": remote_url or local_url,
            "metadata": self._safe_metadata(asset.metadata),
        }

    def _public_remote_url(self, asset: MediaAssetRecord) -> str | None:
        """读取公网 URL。"""
        remote_url = str(asset.metadata.get("remote_url", "")).strip()
        if remote_url.startswith("http://") or remote_url.startswith("https://"):
            return remote_url
        return None

    def _local_preview_url(self, asset: MediaAssetRecord) -> str | None:
        """生成本地预览 URL。"""
        if not self.config.preview_expose_local_media:
            return None

        try:
            resolved_path = self._safe_resolve_local_path(asset)
        except MediaPreviewError:
            return None

        if not resolved_path.exists() or not resolved_path.is_file():
            return None

        prefix = self.config.preview_media_route_prefix
        return f"{prefix}/{asset.id}/file"

    def _safe_resolve_local_path(self, asset: MediaAssetRecord) -> Path:
        """只允许解析项目 outputs 范围内的本地媒体文件。"""
        raw_path = Path(asset.path)
        if str(asset.path).startswith("http://") or str(asset.path).startswith("https://"):
            raise MediaPreviewError("远程媒体不需要本地路径解析")

        candidate = raw_path if raw_path.is_absolute() else self.config.project_root / raw_path
        resolved_candidate = candidate.resolve()
        allowed_roots = [
            (self.config.project_root / "outputs").resolve(),
            self.config.storage_local_upload_dir.resolve(),
        ]

        for allowed_root in allowed_roots:
            try:
                resolved_candidate.relative_to(allowed_root)
                return resolved_candidate
            except ValueError:
                continue

        raise MediaPreviewError("媒体路径不在允许的 outputs 目录内")

    def _safe_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        """过滤不适合给前端展示的大字段。"""
        blocked_keys = {"raw_response", "data", "b64_json"}
        safe: dict[str, Any] = {}
        for key, value in metadata.items():
            if key in blocked_keys:
                continue
            if isinstance(value, str) and len(value) > 500:
                safe[key] = value[:500] + "..."
                continue
            safe[key] = value
        return safe
