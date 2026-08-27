"""生成可供生产页面下载的浏览器扩展 ZIP。"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXTENSION_ROOT = PROJECT_ROOT / "browser-extension" / "job-library"
OUTPUT_ROOT = PROJECT_ROOT / "web-ui" / "public" / "downloads"
PACKAGE_FILES = (
    "manifest.json",
    "content-script.js",
    "service-worker.js",
    "boss-data.js",
    "boss-greeting.js",
    "xiaohongshu-data.js",
    "xiaohongshu-page.js",
)


def package_extension() -> Path:
    manifest = json.loads((EXTENSION_ROOT / "manifest.json").read_text(encoding="utf-8"))
    version = str(manifest["version"]).strip()
    if not version:
        raise ValueError("扩展 manifest 缺少版本号")

    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_ROOT / f"find-job-boss-helper-v{version}.zip"
    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as package:
        for relative_path in PACKAGE_FILES:
            source = EXTENSION_ROOT / relative_path
            if not source.is_file():
                raise FileNotFoundError(f"扩展文件不存在：{source}")
            archive_entry = zipfile.ZipInfo(relative_path, date_time=(2026, 8, 24, 0, 0, 0))
            archive_entry.compress_type = zipfile.ZIP_DEFLATED
            archive_entry.external_attr = 0o644 << 16
            package.writestr(archive_entry, source.read_bytes())
    return output_path


if __name__ == "__main__":
    print(package_extension())
