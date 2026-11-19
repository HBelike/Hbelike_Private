"""生产环境的 Skill 种子与导出工具。

技能库在本地开发时可以读取用户目录中的 ``~/.agents`` 与 ``~/.codex``，但这两类
目录不应成为服务器运行时依赖。本模块复制 ``SKILL.md``、只读 references 文本和许可证；不复制脚本、缓存或凭证。
经过审查的项目种子可复制进持久卷，用户选择的本地 Skill 可导出为项目种子。

它刻意采用“仅缺失时复制”的语义。这样容器重新部署时不会覆盖用户通过 WebUI 保存
的项目本地版本。
"""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path


SKILL_FILE_NAME = "SKILL.md"
_FRONTMATTER_NAME_PATTERN = re.compile(r"^name:\s*(?P<value>.+?)\s*$", re.MULTILINE)
_PORTABLE_SKILL_NAME_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,79}$")


@dataclass(frozen=True)
class SkillPortabilityResult:
    """一次种子复制或导出动作的结果。

    ``copied`` 和 ``skipped`` 都保存相对于目标根目录的路径，便于日志与部署脚本
    输出稳定、且不暴露服务器绝对路径。
    """

    copied: tuple[Path, ...]
    skipped: tuple[Path, ...]

    @property
    def copied_count(self) -> int:
        """返回本次真正写入的文件数量（含引用与许可证）。"""

        return len(self.copied)

    @property
    def skipped_count(self) -> int:
        """返回因目标已存在而保留的 Skill 文件数量。"""

        return len(self.skipped)


def seed_skill_tree(
    *,
    seed_root: Path,
    destination_root: Path,
    overwrite: bool = False,
    owner: str | None = None,
) -> SkillPortabilityResult:
    """把项目随镜像发布的 Skill 种子复制到可写持久目录。

    参数：
    - ``seed_root``：镜像内只读种子根目录；按 ``SKILL.md`` 发现包，并复制支持的只读附带文件。
    - ``destination_root``：通常是 Docker 命名卷挂载的 ``/app/.agents/skills``。
    - ``overwrite``：默认 ``False``，避免部署覆盖 WebUI 已编辑版本。
    - ``owner``：可选 ``user:group``，仅由 root 初始化容器使用，将目标卷改为 API
      进程可写的属主。

    任何越过源/目标根目录的符号链接或路径都会拒绝处理，防止种子流程成为任意文件写入
    通道。失败时抛出异常，使生产初始化容器显式失败，而不是静默产出空 Skill 库。
    """

    resolved_seed_root = _require_directory(seed_root, label="Skill 种子目录")
    resolved_destination_root = _prepare_destination_root(destination_root)
    copied: list[Path] = []
    skipped: list[Path] = []

    for source_path, relative_path in _iter_safe_skill_files(resolved_seed_root):
        result = _copy_skill_package(
            source_path, resolved_destination_root, relative_path.parent, overwrite=overwrite,
        )
        copied.extend(result.copied)
        skipped.extend(result.skipped)

    if owner:
        _change_owner_tree(resolved_destination_root, owner)

    return SkillPortabilityResult(copied=tuple(copied), skipped=tuple(skipped))


def export_skill_trees(
    *,
    source_roots: list[Path],
    destination_root: Path,
    overwrite: bool = False,
) -> SkillPortabilityResult:
    """将指定本地 Skill 根目录导出为可提交的项目种子。

    ``source_roots`` 按顺序处理，并根据 SKILL.md 的 ``name`` 同名去重。导出路径统一
    规整为 ``<skill-name>/SKILL.md``，不把开发机的插件版本、缓存层级或绝对路径带进
    Git。先传入的来源优先，且默认不覆盖项目里已审查的种子。
    """

    if not source_roots:
        raise ValueError("至少需要提供一个 Skill 来源目录。")

    resolved_destination_root = _prepare_destination_root(destination_root)
    copied: list[Path] = []
    skipped: list[Path] = []
    seen_names: set[str] = set()
    for source_root in source_roots:
        resolved_source_root = _require_directory(source_root, label="Skill 来源目录")
        for source_path, relative_path in _iter_safe_skill_files(resolved_source_root):
            skill_name = _portable_skill_name(source_path, fallback=relative_path.parent.name)
            name_key = skill_name.casefold()
            if name_key in seen_names:
                skipped.append(relative_path)
                continue
            seen_names.add(name_key)

            result = _copy_skill_package(
                source_path, resolved_destination_root, Path(skill_name), overwrite=overwrite,
            )
            copied.extend(result.copied)
            skipped.extend(result.skipped)

    return SkillPortabilityResult(copied=tuple(copied), skipped=tuple(skipped))


def portable_skill_files(skill_path: Path) -> tuple[Path, ...]:
    """枚举可部署的提示词、只读引用和许可证，不携带可执行代码或配置。"""
    root = skill_path.parent.resolve()
    candidates = [skill_path]
    candidates.extend(root / name for name in ("LICENSE", "LICENSE.md", "LICENSE.txt", "NOTICE", "NOTICE.md") if (root / name).exists())
    references = root / "references"
    if references.exists():
        candidates.extend(path for path in sorted(references.rglob("*"))
                          if path.is_file() and path.suffix.lower() in {".md", ".json", ".txt"})
    for path in candidates:
        try:
            path.resolve(strict=True).relative_to(root)
        except (OSError, ValueError) as exc:
            raise ValueError("Skill 附带文件越界或不可读取") from exc
    return tuple(candidates)


def _copy_skill_package(source_path: Path, destination_root: Path, destination_directory: Path,
                        *, overwrite: bool) -> SkillPortabilityResult:
    copied, skipped = [], []
    for source in portable_skill_files(source_path):
        relative = destination_directory / source.relative_to(source_path.parent)
        destination = _safe_destination_path(destination_root=destination_root, relative_path=relative)
        if destination.exists() or destination.is_symlink():
            if not overwrite:
                skipped.append(relative)
                continue
            if destination.is_symlink():
                raise ValueError(f"拒绝覆盖 Skill 符号链接：{relative}")
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        copied.append(relative)
    return SkillPortabilityResult(tuple(copied), tuple(skipped))


def _require_directory(path: Path, *, label: str) -> Path:
    """解析并验证一个必须存在的目录。"""

    resolved_path = path.expanduser().resolve()
    if not resolved_path.exists() or not resolved_path.is_dir():
        raise FileNotFoundError(f"{label}不存在或不是目录：{path}")
    return resolved_path


def _prepare_destination_root(path: Path) -> Path:
    """创建目标目录并返回解析后的根目录。"""

    path.expanduser().mkdir(parents=True, exist_ok=True)
    resolved_path = path.expanduser().resolve()
    if not resolved_path.is_dir():
        raise NotADirectoryError(f"Skill 目标路径不是目录：{path}")
    return resolved_path


def _iter_safe_skill_files(root: Path) -> list[tuple[Path, Path]]:
    """返回 root 内安全、确定排序的 ``SKILL.md`` 文件及相对路径。"""

    results: list[tuple[Path, Path]] = []
    for candidate in sorted(root.rglob(SKILL_FILE_NAME), key=lambda item: str(item).casefold()):
        try:
            resolved_candidate = candidate.resolve(strict=True)
            resolved_candidate.relative_to(root)
        except (OSError, ValueError) as exc:
            raise ValueError(f"Skill 种子包含越界或不可读取文件：{candidate}") from exc
        if not resolved_candidate.is_file():
            continue
        relative_path = resolved_candidate.relative_to(root)
        results.append((resolved_candidate, relative_path))
    return results


def _safe_destination_path(*, destination_root: Path, relative_path: Path) -> Path:
    """把相对 Skill 路径映射到目标根目录，并阻断路径穿越。"""

    candidate = destination_root / relative_path
    resolved_candidate = candidate.resolve()
    try:
        resolved_candidate.relative_to(destination_root)
    except ValueError as exc:
        raise ValueError(f"Skill 目标路径越界：{relative_path}") from exc
    return resolved_candidate


def _portable_skill_name(skill_path: Path, *, fallback: str) -> str:
    """读取 frontmatter 的 name 并转换为稳定、安全的种子目录名。"""

    try:
        markdown = skill_path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise ValueError(f"无法读取 Skill 文件：{skill_path}") from exc
    name_match = _FRONTMATTER_NAME_PATTERN.search(markdown)
    raw_name = name_match.group("value").strip().strip("\"'") if name_match else fallback.strip()
    if not _PORTABLE_SKILL_NAME_PATTERN.fullmatch(raw_name):
        raise ValueError(f"Skill name 不适合作为可移植目录名：{raw_name}")
    return raw_name


def _change_owner_tree(root: Path, owner: str) -> None:
    """将目标卷归属交给 API 进程用户；仅 Linux root 初始化容器调用。"""

    try:
        user_name, group_name = owner.split(":", maxsplit=1)
    except ValueError as exc:
        raise ValueError("Skill 卷 owner 必须是 user:group 格式。") from exc
    if not user_name or not group_name:
        raise ValueError("Skill 卷 owner 必须包含用户和用户组。")

    try:
        import grp
        import pwd
    except ImportError as exc:
        raise RuntimeError("当前平台不支持按用户名修改 Skill 卷属主。") from exc

    try:
        user_id = pwd.getpwnam(user_name).pw_uid
        group_id = grp.getgrnam(group_name).gr_gid
    except KeyError as exc:
        raise ValueError(f"Skill 卷 owner 不存在：{owner}") from exc

    paths = [root, *sorted(root.rglob("*"), key=lambda item: str(item).casefold())]
    for path in paths:
        if path.is_symlink():
            continue
        os.chown(path, user_id, group_id)
