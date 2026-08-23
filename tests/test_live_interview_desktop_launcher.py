from __future__ import annotations

from pathlib import Path

import pytest

from src.career_assistant.live_interview.desktop_launcher import (
    DesktopLauncherError,
    launch_windows_desktop_assistant,
)


def _ready_workspace(root: Path) -> Path:
    desktop_root = root / "desktop-interview-assistant"
    electron = desktop_root / "node_modules" / "electron" / "dist" / "electron.exe"
    renderer = desktop_root / "dist-renderer" / "index.html"
    electron.parent.mkdir(parents=True)
    renderer.parent.mkdir(parents=True)
    electron.touch()
    renderer.touch()
    return desktop_root


def test_launcher_starts_built_electron_from_workspace(tmp_path: Path) -> None:
    desktop_root = _ready_workspace(tmp_path)
    calls: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def fake_popen(command, **kwargs):
        calls.append((tuple(command), kwargs))
        return object()

    result = launch_windows_desktop_assistant(
        workspace_root=tmp_path,
        platform_name="win32",
        popen_factory=fake_popen,
    )

    assert result.status == "launching"
    assert calls[0][0] == (
        str(desktop_root / "node_modules" / "electron" / "dist" / "electron.exe"),
        ".",
    )
    assert calls[0][1]["cwd"] == str(desktop_root)
    assert calls[0][1]["close_fds"] is True
    assert calls[0][1]["stdout"] is not None


def test_launcher_uses_electron_cli_for_first_background_download(tmp_path: Path) -> None:
    desktop_root = tmp_path / "desktop-interview-assistant"
    cli = desktop_root / "node_modules" / "electron" / "cli.js"
    renderer = desktop_root / "dist-renderer" / "index.html"
    cli.parent.mkdir(parents=True)
    renderer.parent.mkdir(parents=True)
    cli.touch()
    renderer.touch()
    calls = []

    def fake_popen(command, **kwargs):
        calls.append((tuple(command), kwargs))
        return object()

    result = launch_windows_desktop_assistant(
        workspace_root=tmp_path,
        platform_name="win32",
        node_executable="C:\\NodeJS\\node.exe",
        popen_factory=fake_popen,
    )

    assert result.status == "preparing"
    assert calls[0][0] == ("C:\\NodeJS\\node.exe", str(cli), ".")
    assert "后台准备" in result.message


def test_launcher_rejects_non_windows_platform(tmp_path: Path) -> None:
    with pytest.raises(DesktopLauncherError, match="仅支持 Windows"):
        launch_windows_desktop_assistant(
            workspace_root=tmp_path,
            platform_name="linux",
        )


def test_launcher_explains_missing_electron_package(tmp_path: Path) -> None:
    renderer = tmp_path / "desktop-interview-assistant" / "dist-renderer" / "index.html"
    renderer.parent.mkdir(parents=True)
    renderer.touch()
    with pytest.raises(DesktopLauncherError, match="尚未安装"):
        launch_windows_desktop_assistant(
            workspace_root=tmp_path,
            platform_name="win32",
            node_executable="C:\\NodeJS\\node.exe",
        )
