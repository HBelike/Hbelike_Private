from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from src.providers.seedream_provider import SeedreamImageResult
from src.repositories.generated_content_repository import GeneratedContentForImage
from src.services.github_image_upgrade_service import GitHubImageUpgradeService
from src.tasks.image_task import ImageTask


def test_manual_github_image_upgrade_honors_dynamic_project_count(tmp_path: Path) -> None:
    repositories = [f"owner/repo-{index}" for index in range(1, 7)]
    content = GeneratedContentForImage(
        id=17,
        week_end="2026-08-14",
        title="Top 6",
        image_prompts=[
            {
                "repository_full_name": repository_full_name,
                "prompt": f"{repository_full_name} 架构图",
            }
            for repository_full_name in repositories
        ],
    )
    content_repository = Mock()
    content_repository.get_for_image_generation.return_value = content
    media_asset_repository = Mock()
    media_asset_repository.list_by_content_id.return_value = []
    media_asset_repository.mark_replaced_by_ids.return_value = 0
    provider = Mock()
    provider.find_and_download_image.return_value = None

    with (
        patch(
            "src.services.github_image_upgrade_service.GeneratedContentRepository",
            return_value=content_repository,
        ),
        patch(
            "src.services.github_image_upgrade_service.MediaAssetRepository",
            return_value=media_asset_repository,
        ),
        patch(
            "src.services.github_image_upgrade_service.GitHubRepositoryAssetProvider",
            return_value=provider,
        ),
    ):
        result = GitHubImageUpgradeService(
            config=SimpleNamespace(image_output_dir=tmp_path),
            database_manager=SimpleNamespace(),
        ).upgrade_content_images(content_id=17)

    assert result.prompt_count == 6
    assert result.not_found_repositories == repositories
    assert provider.find_and_download_image.call_count == 6


def test_image_task_generates_one_image_for_each_dynamic_project(tmp_path: Path) -> None:
    repositories = [f"owner/repo-{index}" for index in range(1, 7)]
    content = GeneratedContentForImage(
        id=17,
        week_end="2026-08-14",
        title="Top 6",
        image_prompts=[
            {
                "repository_full_name": repository_full_name,
                "prompt": f"{repository_full_name} 架构图",
                "summary_text": f"{repository_full_name} 的架构与主数据流。",
            }
            for repository_full_name in repositories
        ],
    )
    content_repository = Mock()
    content_repository.latest_for_image_generation.return_value = content
    media_asset_repository = Mock()
    media_asset_repository.list_by_content_id.return_value = []
    media_asset_repository.mark_replaced_by_ids.return_value = 0
    media_asset_repository.create.side_effect = [
        SimpleNamespace(
            id=100 + index,
            path=str(tmp_path / f"top{index}.png"),
            provider="seedream",
        )
        for index in range(1, 7)
    ]
    storyboard_repository = Mock()
    storyboard_repository.latest_for_content.return_value = None
    provider = Mock()
    provider.has_api_key.return_value = True
    provider.generate_image.side_effect = lambda prompt, output_path: SeedreamImageResult(
        output_path=output_path,
        source_url=None,
        raw_response={},
    )
    prompt_design_service = Mock()
    prompt_design_service.build_project_architecture_prompt.return_value = "项目架构与主数据流"
    config = SimpleNamespace(
        project_root=tmp_path,
        image_output_dir=tmp_path,
        image_refresh_existing_assets_on_run=False,
        image_github_asset_fallback_enabled=False,
        image_paid_generation_enabled=True,
        image_local_fallback_enabled=False,
        image_provider="seedream",
        image_model="stub-seedream",
        image_size="2048x2048",
        image_prompt_max_length=5000,
        runtime_prompt=lambda _name: "",
    )
    context = SimpleNamespace(config=config, database_manager=SimpleNamespace())
    task = object.__new__(ImageTask)
    task.logger = logging.getLogger("test.image.dynamic-project-count")

    with (
        patch("src.tasks.image_task.GeneratedContentRepository", return_value=content_repository),
        patch("src.tasks.image_task.MediaAssetRepository", return_value=media_asset_repository),
        patch("src.tasks.image_task.VideoStoryboardRepository", return_value=storyboard_repository),
        patch("src.tasks.image_task.SeedreamProvider", return_value=provider),
        patch("src.tasks.image_task.ImagePromptDesignService", return_value=prompt_design_service),
    ):
        result = task.execute(context)

    assert result["prompt_count"] == 6
    assert result["created_image_count"] == 6
    assert provider.generate_image.call_count == 6
    assert media_asset_repository.create.call_count == 6
