"""从本地求职助手服务启动 Windows 实时面试采集器。"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class DesktopLauncherError(RuntimeError):
    """桌面采集器当前无法启动。"""


@dataclass(frozen=True)
class DesktopLaunchResult:
    status: str
    message: str


PopenFactory = Callable[..., Any]


def launch_windows_desktop_assistant(
    *,
    workspace_root: Path | None = None,
    platform_name: str | None = None,
    node_executable: str | None = None,
    popen_factory: PopenFactory = subprocess.Popen,
) -> DesktopLaunchResult:
    """启动 Electron 采集器；重复启动由 Electron 单实例机制负责聚焦。"""

    active_platform = platform_name or sys.platform
    if active_platform != "win32":
        raise DesktopLauncherError("面试大师的双路音频采集目前仅支持 Windows 10/11")

    root = workspace_root or Path(__file__).resolve().parents[3]
    desktop_root = root / "desktop-interview-assistant"
    electron_executable = desktop_root / "node_modules" / "electron" / "dist" / "electron.exe"
    electron_cli = desktop_root / "node_modules" / "electron" / "cli.js"
    renderer_entry = desktop_root / "dist-renderer" / "index.html"

    if not renderer_entry.is_file():
        raise DesktopLauncherError(
            "Windows 采集组件尚未构建，请在 desktop-interview-assistant 中执行 npm run build"
        )

    preparing = not electron_executable.is_file()
    if preparing:
        active_node = node_executable or shutil.which("node.exe") or shutil.which("node")
        if not electron_cli.is_file() or not active_node:
            raise DesktopLauncherError(
                "Windows 采集组件尚未安装，请先在 desktop-interview-assistant 中执行 npm ci"
            )
        # Electron 43 的 CLI 会在二进制缺失时完成首次下载，随后继续启动窗口。
        command: Sequence[str] = (active_node, str(electron_cli), ".")
    else:
        command = (str(electron_executable), ".")
    creation_flags = (
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        | getattr(subprocess, "DETACHED_PROCESS", 0)
    )
    try:
        popen_factory(
            command,
            cwd=str(desktop_root),
            close_fds=True,
            creationflags=creation_flags,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        raise DesktopLauncherError("Windows 采集组件启动失败，请重新安装桌面依赖") from exc

    return DesktopLaunchResult(
        status="preparing" if preparing else "launching",
        message=(
            "首次启动正在后台准备 Windows 采集组件，完成后会自动打开面试大师"
            if preparing
            else "面试大师正在启动；若窗口已经打开，将自动切换到现有窗口"
        ),
    )
