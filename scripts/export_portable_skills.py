"""将经过用户选择的本地 Skill 导出为可随项目部署的种子。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.services.skill_portability import export_skill_trees


def parse_args() -> argparse.Namespace:
    """解析导出参数；来源显式指定，避免无意复制整个用户目录。"""

    parser = argparse.ArgumentParser(description="导出本地 SKILL.md 到项目部署种子目录。")
    parser.add_argument(
        "--source-root",
        type=Path,
        action="append",
        required=True,
        help="需要导出的 Skill 根目录。可重复指定，按传入顺序去重。",
    )
    parser.add_argument(
        "--destination-root",
        type=Path,
        default=PROJECT_ROOT / "deploy" / "skill-seeds",
        help="项目内种子目录。",
    )
    parser.add_argument("--overwrite", action="store_true", help="明确允许覆盖已审查的种子文件。")
    return parser.parse_args()


def main() -> int:
    """执行导出并输出相对文件清单。"""

    args = parse_args()
    result = export_skill_trees(
        source_roots=args.source_root,
        destination_root=args.destination_root,
        overwrite=args.overwrite,
    )
    print(
        json.dumps(
            {
                "event": "portable_skill_export_completed",
                "copied": result.copied_count,
                "skipped_existing": result.skipped_count,
                "copied_paths": [str(item).replace("\\", "/") for item in result.copied],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
