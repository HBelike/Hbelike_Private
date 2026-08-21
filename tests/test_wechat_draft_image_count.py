from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.config.config_manager import AppConfig
from src.repositories.article_layout_repository import ArticleLayoutRecord
from src.repositories.media_asset_repository import MediaAssetRecord
from src.services.article_layout_service import (
    ArticleLayoutBuildResult,
    ArticleLayoutService,
    resolve_expected_project_image_count,
)
from src.services.wechat_draft_preflight_service import WechatDraftPreflightService


def _config(project_root: Path) -> AppConfig:
    return AppConfig(
        project_root=project_root,
        config_path=project_root / "config" / "app.yaml",
        raw={
            "wechat": {
                "enabled": True,
                "require_cover_asset": True,
                "require_public_article_images": False,
                "require_local_uploadable_images": True,
                "require_video_asset": False,
                "require_local_uploadable_video": False,
            }
        },
    )


def _layout(expected_image_count: int | None) -> ArticleLayoutRecord:
    layout_stats: dict[str, int] = {
        "embedded_image_count": 0,
        "missing_image_count": 1,
    }
    if expected_image_count is not None:
        layout_stats["expected_image_count"] = expected_image_count
    return ArticleLayoutRecord(
        id=1,
        content_id=17,
        title="Top1",
        digest="digest",
        article_html="<section>content</section>",
        cover_asset_id=101,
        payload={"layout_stats": layout_stats},
        status="ready",
        created_at="2026-08-20T00:00:00Z",
        updated_at="2026-08-20T00:00:00Z",
    )


def _image_asset(project_root: Path) -> MediaAssetRecord:
    image_path = project_root / "outputs" / "images" / "top1.png"
    image_path.parent.mkdir(parents=True)
    image_path.write_bytes(b"image")
    return MediaAssetRecord(
        id=101,
        content_id=17,
        asset_type="image",
        provider="seedream",
        path=str(image_path),
        mime_type="image/png",
        status="created",
        metadata={"repository_full_name": "owner/repo"},
    )


class WechatDraftImageCountTest(unittest.TestCase):
    def test_top1_layout_accepts_one_uploadable_image(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir, patch.dict(
            os.environ,
            {"WECHAT_APP_ID": "configured", "WECHAT_APP_SECRET": "configured"},
        ):
            project_root = Path(temporary_dir)
            result = WechatDraftPreflightService().build(
                config=_config(project_root),
                layout=_layout(expected_image_count=1),
                media_assets=[_image_asset(project_root)],
            )

        self.assertTrue(result.can_call_wechat_api)
        self.assertEqual(result.missing_requirements, [])
        self.assertEqual(result.payload["assets"]["expected_image_count"], 1)

    def test_preflight_reports_actual_expected_image_count(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir, patch.dict(
            os.environ,
            {"WECHAT_APP_ID": "configured", "WECHAT_APP_SECRET": "configured"},
        ):
            project_root = Path(temporary_dir)
            result = WechatDraftPreflightService().build(
                config=_config(project_root),
                layout=_layout(expected_image_count=2),
                media_assets=[_image_asset(project_root)],
            )

        self.assertFalse(result.can_call_wechat_api)
        self.assertIn("可上传到微信的本地项目图不足 2 张：当前 1 张", result.missing_requirements)

    def test_legacy_layout_infers_image_count_from_layout_stats(self) -> None:
        self.assertEqual(resolve_expected_project_image_count(_layout(expected_image_count=None).payload), 1)

    def test_layout_payload_persists_expected_image_count(self) -> None:
        result = ArticleLayoutBuildResult(
            article_html="<section />",
            cover_asset_id=101,
            expected_image_count=1,
            embedded_image_count=0,
            missing_image_count=1,
            block_count=2,
            style_version="test",
        )
        payload = ArticleLayoutService().build_payload(
            content=type(
                "Content",
                (),
                {
                    "id": 17,
                    "week_end": "2026-08-14",
                    "status": "approved",
                    "updated_at": "2026-08-20T00:00:00Z",
                },
            )(),
            result=result,
            media_assets=[],
        )

        self.assertEqual(payload["layout_stats"]["expected_image_count"], 1)


if __name__ == "__main__":
    unittest.main()
