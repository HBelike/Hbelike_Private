"""从固定提交和当前生产镜像生成可审查的实时面试精确补丁包。"""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime, timezone


BASE_API = "wechat-agent-career-api:release-local-baa5f320b873"
BASE_API_ID = "sha256:5a8fcc1f2f0e8a7802709e9b2f080af10e9fc2b8fe7b34727ba7d0d57d78afe6"
BASE_WEB = "wechat-agent-career-web:release-local-baa5f320b873"
BASE_WEB_ID = "sha256:2a5b31c758d9e243604899f70fe9f81e677c2b0db793e8765a86df00bdeb3fd6"
PATCH_FILES = (
    "src/career_assistant/live_interview/web.py",
    "src/career_assistant/live_interview/audio_relay.py",
    "src/career_assistant/live_interview/session_manager.py",
    "src/career_assistant/live_interview/answer_service.py",
    "src/career_assistant/live_interview/asr/dashscope_realtime.py",
)


def run(*args: str, cwd: Path | None = None) -> bytes:
    return subprocess.check_output(args, cwd=cwd)


def sha(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--checks-file", type=Path, required=True)
    parser.add_argument("--configs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    # 复用发布技能的检查指纹和凭据扫描，不能自行填造检查结果。
    sys.path.insert(0, str(Path.home() / ".codex/skills/wechat-release/scripts"))
    from common import scan_secrets, work_fingerprint

    checks = json.loads(args.checks_file.read_text(encoding="utf-8"))
    if not checks.get("ok") or checks.get("fingerprint") != work_fingerprint(root):
        raise RuntimeError("必须先通过与当前工作区相符的正式发布检查")
    revision = run("git", "rev-parse", "HEAD", cwd=root).decode().strip()
    branch = run("git", "branch", "--show-current", cwd=root).decode().strip()
    for name, expected in ((BASE_API, BASE_API_ID), (BASE_WEB, BASE_WEB_ID)):
        actual = run("docker", "image", "inspect", name, "--format", "{{.Id}}").decode().strip()
        if actual != expected:
            raise RuntimeError("本机镜像与当前生产基线不一致：" + name)
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    tag = "git-" + revision[:12]
    image = "wechat-agent-career-api:" + tag
    hashes = {}
    with tempfile.TemporaryDirectory(prefix="hotfix-", dir=output) as temporary:
        context = Path(temporary)
        for name in (*PATCH_FILES, "docker/Dockerfile.live-interview-hotfix"):
            raw = run("git", "show", revision + ":" + name, cwd=root)
            working = (root / name).read_bytes()
            if raw.replace(b"\r\n", b"\n") != working.replace(b"\r\n", b"\n"):
                raise RuntimeError("补丁文件未完整提交：" + name)
            scan_secrets(root, name, raw)
            destination = context / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
            hashes[name] = sha(destination)
        with (output / "build-api.log").open("wb") as log:
            subprocess.run([
                "docker", "build", "--platform", "linux/amd64", "--pull=false",
                "--label", "org.opencontainers.image.revision=" + revision,
                "-f", str(context / "docker/Dockerfile.live-interview-hotfix"),
                "-t", image, str(context),
            ], check=True, stdout=log, stderr=subprocess.STDOUT)
    api_id = run("docker", "image", "inspect", image, "--format", "{{.Id}}").decode().strip()
    run("docker", "run", "--rm", "--network", "none", "--entrypoint", "python", image,
        "-c", "from src.web.api import create_app; from src.career_assistant.live_interview.web import router")
    archive = output / "images.tar.gz"
    with gzip.open(archive, "wb", compresslevel=1) as target:
        process = subprocess.Popen(["docker", "save", image, BASE_WEB], stdout=subprocess.PIPE)
        assert process.stdout is not None
        shutil.copyfileobj(process.stdout, target, length=1024 * 1024)
        process.stdout.close()
        if process.wait():
            raise RuntimeError("镜像导出失败")
    configs = {}
    for name in ("docker-compose.production.yml", "Caddyfile"):
        shutil.copyfile(args.configs / name, output / name)
        configs[name] = sha(output / name)
    extension = root / "tmp/live-interview-hotfix/production-extension.json"
    extension_data = json.loads(extension.read_text(encoding="utf-8"))
    manifest = {
        "schema": 1, "commit": revision, "branch": branch, "tag": tag,
        "created": datetime.now(timezone.utc).isoformat(), "checks": checks,
        "source_fingerprint": checks["fingerprint"],
        "release_kind": "live-interview-api-hotfix", "patch_files": hashes,
        "base_api": {"name": BASE_API, "id": BASE_API_ID},
        "images": {"api": {"name": image, "id": api_id},
                   "web": {"name": BASE_WEB, "id": BASE_WEB_ID}},
        "extension": extension_data, "configs": configs,
        "archive": {"name": archive.name, "bytes": archive.stat().st_size, "sha256": sha(archive)},
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"prepared": True, "manifest": str(output / "manifest.json"), "api_id": api_id}))


if __name__ == "__main__":
    main()
