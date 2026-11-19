from __future__ import annotations

import json
import posixpath
import re
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXTENSION_ROOT = PROJECT_ROOT / "browser-extension" / "job-library"
PUBLIC_ROOT = PROJECT_ROOT / "web-ui" / "public"


def test_downloadable_extension_matches_manifest_and_has_flat_root() -> None:
    manifest = json.loads((EXTENSION_ROOT / "manifest.json").read_text(encoding="utf-8"))
    archive = PUBLIC_ROOT / "downloads" / f"find-job-boss-helper-v{manifest['version']}.zip"

    assert archive.is_file()
    with zipfile.ZipFile(archive) as package:
        names = set(package.namelist())
        assert {
            "manifest.json",
            "content-script.js",
            "service-worker.js",
            "boss-data.js",
            "boss-greeting.js",
            "boss-chat.js",
            "boss-chat-page.js",
            "boss-chat-control.js",
            "boss-chat-dispatch.js",
            "boss-chat-reply-page.js",
            "xiaohongshu-data.js",
            "xiaohongshu-page.js",
        }.issubset(names)
        packaged_manifest = json.loads(package.read("manifest.json").decode("utf-8"))
        for name in names:
            assert package.read(name) == (EXTENSION_ROOT / name).read_bytes()
    assert packaged_manifest["version"] == manifest["version"]


def test_installation_guide_covers_both_supported_browsers() -> None:
    guide = (PUBLIC_ROOT / "boss-extension-guide.html").read_text(encoding="utf-8")
    manifest = json.loads((EXTENSION_ROOT / "manifest.json").read_text(encoding="utf-8"))

    assert "Google Chrome" in guide
    assert "Microsoft Edge" in guide
    assert "chrome://extensions" in guide
    assert "edge://extensions" in guide
    assert 'href="/interviews/jobs"' in guide
    assert f"find-job-boss-helper-v{manifest['version']}.zip" in guide
    assert "加载已解压的扩展程序" in guide


def test_downloadable_extension_contains_every_runtime_module_dependency() -> None:
    manifest = json.loads((EXTENSION_ROOT / "manifest.json").read_text(encoding="utf-8"))
    archive = PUBLIC_ROOT / "downloads" / f"find-job-boss-helper-v{manifest['version']}.zip"
    with zipfile.ZipFile(archive) as package:
        names = set(package.namelist())
        for name in names:
            if not name.endswith(".js"):
                continue
            source = package.read(name).decode("utf-8")
            for dependency in re.findall(r"(?:\bfrom\s*|\bimport\s*)['\"](\.[^'\"]+)['\"]", source):
                target = posixpath.normpath(posixpath.join(posixpath.dirname(name), dependency))
                assert target in names, f"发行包缺少 {name} 依赖的运行模块：{target}"
        # 当前源码入口必须也在包中，不能用旧 SW 的旧依赖图掩盖漏包。
        assert package.read("service-worker.js") == (EXTENSION_ROOT / "service-worker.js").read_bytes()
