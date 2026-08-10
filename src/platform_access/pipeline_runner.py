"""管理员手动触发内容流水线的后台执行器。"""

from __future__ import annotations

from pathlib import Path
from threading import Lock, Thread
from typing import Any

from src.app.application import Application
from src.platform_access.contracts import PlatformUser
from src.platform_access.service import PlatformAccessService
from src.tasks.task_result import TaskResult


class ManualPipelineRunner:
    """将一次管理员点击转换为可追踪、可恢复查看的后台流水线执行。

    Web 请求只负责登记和启动线程，不等待长时间的 GitHub、模型或媒体调用，
    因此不会触发浏览器超时。每次运行都使用提交时冻结的配置快照。
    """

    def __init__(self, project_root: Path, access_service: PlatformAccessService) -> None:
        self._project_root = project_root
        self._access_service = access_service
        self._active_request_ids: set[str] = set()
        self._lock = Lock()

    def request_run(self, user: PlatformUser, *, idempotency_key: str) -> dict[str, object]:
        """登记手动运行；同一幂等键重复提交只返回原记录，不重复启动任务。"""

        request, config_item = self._access_service.create_manual_pipeline_request(
            user,
            idempotency_key=idempotency_key,
        )
        request_id = str(request["id"])
        should_start = False
        with self._lock:
            if request["status"] == "queued" and request_id not in self._active_request_ids:
                self._active_request_ids.add(request_id)
                should_start = True
        if should_start:
            thread = Thread(
                target=self._run,
                args=(user, request_id, config_item.get("config", {})),
                name=f"manual-pipeline-{request_id[:8]}",
                daemon=True,
            )
            thread.start()
        return request

    def list_runs(self, user: PlatformUser) -> list[dict[str, object]]:
        """返回当前账号可见的最近运行历史。"""

        return self._access_service.list_manual_pipeline_requests(user)

    def _run(self, user: PlatformUser, request_id: str, runtime_config: object) -> None:
        """在线程内执行真实流水线，并把每个 Task 的结果收敛为审计摘要。"""

        try:
            self._access_service.update_manual_pipeline_request(user, request_id, status="running")
            if not isinstance(runtime_config, dict):
                raise ValueError("运行配置快照无效")

            results = Application(
                self._project_root,
                runtime_config=runtime_config,
            ).run_manual_pipeline()
            payload = {"tasks": [_serialize_task_result(item) for item in results]}
            self._access_service.update_manual_pipeline_request(
                user,
                request_id,
                status="succeeded",
                metadata=payload,
            )
        except Exception as exc:  # 后台任务必须把故障回写，而不是静默丢失。
            self._access_service.update_manual_pipeline_request(
                user,
                request_id,
                status="failed",
                error_message=_safe_error_message(exc),
            )
        finally:
            with self._lock:
                self._active_request_ids.discard(request_id)


def _serialize_task_result(result: TaskResult) -> dict[str, Any]:
    """去除可能很大的中间对象，仅保存管理台需要的任务审计信息。"""

    return {
        "task_name": result.task_name,
        "run_id": result.run_id,
        "metadata_keys": sorted(str(key) for key in result.metadata.keys()),
    }


def _safe_error_message(error: Exception) -> str:
    """控制返回给管理台的错误长度，避免把敏感参数或长堆栈写进数据库。"""

    text = str(error).strip() or error.__class__.__name__
    return text[:600]
