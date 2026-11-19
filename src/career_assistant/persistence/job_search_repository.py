"""独立搜索批次及岗位版本；写入锁顺序统一为会话、批次。"""
from contextlib import nullcontext
from datetime import datetime
import json
from uuid import UUID, uuid4

from sqlalchemy import text

from src.career_assistant.job_search_sessions import normalize_conditions, normalize_job, operation_digest


class JobSearchRepository:
    def __init__(self, database):
        self._database = database

    def _conversation(self, connection, actor_id, conversation_id, *, write=False):
        row = connection.execute(text("""
            SELECT id,status FROM career_assistant.conversations
            WHERE id=:conversation_id AND actor_id=:actor_id AND status<>'deleted'
        """ + (" FOR UPDATE" if write else "")),
            {"actor_id": actor_id, "conversation_id": conversation_id}).mappings().one_or_none()
        if row is None or (write and row["status"] != "active"):
            raise LookupError("会话不存在或不可操作")

    def _batch(self, connection, conversation_id, batch_id, *, lock=False):
        row = connection.execute(text("""
            SELECT * FROM career_assistant.job_search_batches
            WHERE id=:id AND conversation_id=:conversation_id
        """ + (" FOR UPDATE" if lock else "")), {"id": batch_id, "conversation_id": conversation_id}).mappings().one_or_none()
        if row is None:
            raise LookupError("搜索批次不存在")
        return dict(row)

    @staticmethod
    def _public(row):
        return {key: str(value) if isinstance(value, UUID) else value.isoformat() if isinstance(value, datetime) else value
                for key, value in dict(row).items() if key != "operation_receipts"}

    def _source(self, connection, conversation_id, message_id):
        row = connection.execute(text("""
            SELECT message.id,message.turn_id,message.ui_payload,
                   turn.requested_selection_mode,turn.requested_model_profile_id,
                   payload.request_metadata_json,payload.input_text
            FROM career_assistant.messages AS message
            LEFT JOIN career_assistant.agent_turns AS turn ON turn.id=message.turn_id
            LEFT JOIN career_assistant.agent_turn_payloads AS payload ON payload.turn_id=turn.id
            WHERE message.id=:id AND message.conversation_id=:conversation_id AND message.role='assistant'
        """), {"id": message_id, "conversation_id": conversation_id}).mappings().one_or_none()
        if row is None or (row["ui_payload"] or {}).get("type") != "job_search":
            raise LookupError("求职卡片不存在")
        return row

    def _insert(self, connection, conversation_id, source, request, *, legacy=False):
        number = connection.execute(text("SELECT COALESCE(MAX(batch_no),0)+1 FROM career_assistant.job_search_batches WHERE conversation_id=:id"), {"id": conversation_id}).scalar_one()
        payload = source["ui_payload"] or {}
        context = {"metadata": source["request_metadata_json"] or {},
                   "text": source["input_text"] or "",
                   "model_selection": {"mode": source["requested_selection_mode"] or "platform_default",
                                       "profile_id": str(source["requested_model_profile_id"]) if source["requested_model_profile_id"] else None}}
        requested = request.get("analysis_requested", payload.get("auto_analyze", False))
        if type(requested) is not bool:
            raise ValueError("自动分析标记无效")
        row = connection.execute(text("""
            INSERT INTO career_assistant.job_search_batches
            (id,conversation_id,source_message_id,source_turn_id,batch_no,conditions,analysis_requested,analysis_context,legacy_imported,legacy_total_count)
            VALUES (:id,:conversation_id,:message_id,:turn_id,:batch_no,CAST(:conditions AS jsonb),:requested,CAST(:context AS jsonb),:legacy,:total)
            RETURNING *
        """), {"id": uuid4(), "conversation_id": conversation_id, "message_id": source["id"], "turn_id": source["turn_id"],
                "batch_no": number, "conditions": json.dumps(normalize_conditions(request.get("conditions", payload.get("conditions", {}))), ensure_ascii=False),
                "requested": requested and not legacy, "context": json.dumps(context, ensure_ascii=False), "legacy": legacy,
                "total": (payload.get("result_summary") or {}).get("count") if legacy else None}).mappings().one()
        return dict(row)

    def _import_legacy(self, connection, conversation_id):
        sources = connection.execute(text("""
            SELECT message.id FROM career_assistant.messages AS message
            WHERE message.conversation_id=:id AND message.role='assistant'
              AND message.ui_payload->>'type'='job_search'
              AND message.ui_payload->>'version'='1'
              AND message.ui_payload->'result_summary'->'jobs' IS NOT NULL
              AND NOT EXISTS(SELECT 1 FROM career_assistant.job_search_batches AS batch WHERE batch.source_message_id=message.id)
            ORDER BY message.created_at,message.id
        """), {"id": conversation_id}).mappings().all()
        for item in sources:
            source = self._source(connection, conversation_id, item["id"])
            batch = self._insert(connection, conversation_id, source, {}, legacy=True)
            jobs = (source["ui_payload"].get("result_summary") or {}).get("jobs", [])
            self._write_jobs(connection, batch, jobs)
            connection.execute(text("UPDATE career_assistant.job_search_batches SET search_status=:status WHERE id=:id"),
                               {"id": batch["id"], "status": "succeeded" if jobs else "empty"})

    def create_batch(self, actor_id, conversation_id, request):
        with self._database.transaction() as connection:
            self._conversation(connection, actor_id, conversation_id, write=True)
            self._import_legacy(connection, conversation_id)
            source = self._source(connection, conversation_id, request.get("source_message_id"))
            request_id = self._request_id(request)
            existing = connection.execute(text("""
                SELECT * FROM career_assistant.job_search_batches WHERE conversation_id=:conversation_id
                AND operation_receipts ? :key
            """), {"conversation_id": conversation_id, "key": request_id}).mappings().one_or_none()
            if existing:
                return self._replay(dict(existing), request, "create")
            batch = self._insert(connection, conversation_id, source, request)
            return self._receipt(connection, batch, request, "create", {})

    def list_batches(self, actor_id, conversation_id):
        with self._database.transaction() as connection:
            self._conversation(connection, actor_id, conversation_id, write=True)
            self._import_legacy(connection, conversation_id)
            return self._catalog(connection, conversation_id)

    def _catalog(self, connection, conversation_id):
        return [self._public(row) for row in connection.execute(text("SELECT * FROM career_assistant.job_search_batches WHERE conversation_id=:id ORDER BY batch_no"), {"id": conversation_id}).mappings()]

    @staticmethod
    def _jobs(connection, batch_id, revision, *, cursor=0, limit=None):
        pagination = " LIMIT :limit OFFSET :cursor" if limit is not None else ""
        rows = connection.execute(text("""
            SELECT * FROM (SELECT DISTINCT ON(job_ref) job_ref,display_index,bridge_key,payload,revision
                FROM career_assistant.job_search_job_versions WHERE batch_id=:id AND revision<=:revision
                ORDER BY job_ref,revision DESC) AS latest ORDER BY display_index,job_ref
        """ + pagination), {"id": batch_id, "revision": revision, "cursor": cursor, "limit": limit}).mappings()
        return [{**row["payload"], "job_ref": row["job_ref"], "display_index": row["display_index"],
                 "bridge_key": row["bridge_key"]} for row in rows]

    def _write_jobs(self, connection, batch, values, *, details=False):
        if not isinstance(values, list) or len(values) > 50:
            raise ValueError("每次最多保存 50 个岗位")
        old_jobs = self._jobs(connection, batch["id"], batch["revision"])
        by_key = {job["bridge_key"]: job for job in old_jobs}
        by_ref = {job["job_ref"]: job for job in old_jobs}
        changes, refs = {}, {}
        for raw in values:
            if not isinstance(raw, dict):
                raise ValueError("岗位格式无效")
            previous = by_ref.get(raw.get("job_ref")) if details else by_key.get(str(raw.get("jobId") or raw.get("bridge_key") or raw.get("id") or raw.get("securityId") or ""))
            if details and previous is None:
                raise ValueError("详情引用的岗位不存在")
            merged = {**(previous or {}), **raw}
            # 基础列表重传不能抹掉已取得的完整 JD。
            if not details and previous and previous.get("description") and not raw.get("description"):
                merged["description"] = previous["description"]
                merged["detail_status"] = previous.get("detail_status", "ready")
            normalized = normalize_job(merged)
            if previous:
                reference, position = previous["job_ref"], previous["display_index"]
            else:
                position = len(by_ref) + 1
                reference = f'S{batch["batch_no"]}-J{position}'
            normalized.update(job_ref=reference, display_index=position)
            refs[normalized["bridge_key"]] = reference
            if normalized != previous:
                changes[reference] = normalized
            by_ref[reference] = normalized
            by_key[normalized["bridge_key"]] = normalized
        if changes:
            revision = batch["revision"] + 1
            for job in changes.values():
                payload = {key: value for key, value in job.items() if key not in ("job_ref", "display_index", "bridge_key")}
                connection.execute(text("""
                    INSERT INTO career_assistant.job_search_job_versions(batch_id,job_ref,revision,display_index,bridge_key,payload)
                    VALUES (:id,:ref,:revision,:position,:key,CAST(:payload AS jsonb))
                """), {"id": batch["id"], "ref": job["job_ref"], "revision": revision, "position": job["display_index"],
                        "key": job["bridge_key"], "payload": json.dumps(payload, ensure_ascii=False)})
            batch.update(revision=revision, loaded_count=len(by_ref))
            connection.execute(text("UPDATE career_assistant.job_search_batches SET revision=:revision,loaded_count=:count,updated_at=NOW() WHERE id=:id"),
                               {"id": batch["id"], "revision": revision, "count": len(by_ref)})
        return refs

    @staticmethod
    def _request_id(request):
        value = request.get("request_id")
        if not isinstance(value, str) or not value.strip() or len(value) > 120:
            raise ValueError("缺少有效的 request_id")
        return value

    def _replay(self, batch, request, kind):
        receipt = batch["operation_receipts"].get(self._request_id(request))
        if receipt is None:
            return None
        if receipt["digest"] != operation_digest(request) or receipt["kind"] != kind:
            raise ValueError("同一 request_id 不能用于不同内容")
        return {"batch": self._public(batch), "revision": receipt["revision"], "job_refs": receipt["job_refs"]}

    def _receipt(self, connection, batch, request, kind, refs):
        receipts = dict(batch["operation_receipts"])
        receipts[self._request_id(request)] = {"digest": operation_digest(request), "kind": kind, "revision": batch["revision"], "job_refs": refs}
        connection.execute(text("UPDATE career_assistant.job_search_batches SET operation_receipts=CAST(:receipts AS jsonb),updated_at=NOW() WHERE id=:id"),
                           {"id": batch["id"], "receipts": json.dumps(receipts, ensure_ascii=False)})
        batch["operation_receipts"] = receipts
        return {"batch": self._public(batch), "revision": batch["revision"], "job_refs": refs}

    def _save(self, actor_id, conversation_id, batch_id, request, *, details=False):
        with self._database.transaction() as connection:
            self._conversation(connection, actor_id, conversation_id, write=True)
            batch = self._batch(connection, conversation_id, batch_id, lock=True)
            kind = "details" if details else "page"
            replay = self._replay(batch, request, kind)
            if replay:
                return replay
            if batch["search_status"] == "cancelled":
                raise ValueError("搜索批次已取消")
            if not details:
                if type(request.get("page")) is not int or request["page"] < 1:
                    raise ValueError("分页参数无效")
                if type(request.get("has_more", False)) is not bool:
                    raise ValueError("分页状态无效")
            refs = self._write_jobs(connection, batch, request.get("jobs"), details=details)
            if not details:
                batch.update(search_status="succeeded" if batch["loaded_count"] else "empty", has_more=request.get("has_more", False))
                connection.execute(text("UPDATE career_assistant.job_search_batches SET search_status=:status,has_more=:more WHERE id=:id"),
                                   {"id": batch_id, "status": batch["search_status"], "more": batch["has_more"]})
            return self._receipt(connection, batch, request, kind, refs)

    def save_page(self, actor_id, conversation_id, batch_id, request):
        return self._save(actor_id, conversation_id, batch_id, request)

    def save_details(self, actor_id, conversation_id, batch_id, request):
        return self._save(actor_id, conversation_id, batch_id, request, details=True)

    def focus(self, actor_id, conversation_id, batch_id, request):
        with self._database.transaction() as connection:
            self._conversation(connection, actor_id, conversation_id, write=True)
            batch = self._batch(connection, conversation_id, batch_id, lock=True)
            reference = request.get("job_ref")
            if reference is not None and reference not in {job["job_ref"] for job in self._jobs(connection, batch_id, batch["revision"])}:
                raise ValueError("预览岗位不存在")
            connection.execute(text("UPDATE career_assistant.job_search_batches SET focused_job_ref=:ref,last_focused_at=NOW() WHERE id=:id"), {"id": batch_id, "ref": reference})
            batch = self._batch(connection, conversation_id, batch_id)
            return {"batch": self._public(batch), "revision": batch["revision"], "job_refs": {}}

    def complete(self, actor_id, conversation_id, batch_id, request):
        with self._database.transaction() as connection:
            self._conversation(connection, actor_id, conversation_id, write=True)
            batch = self._batch(connection, conversation_id, batch_id, lock=True)
            replay = self._replay(batch, request, "complete")
            if replay:
                return replay
            status = request.get("search_status", request.get("status", "succeeded" if batch["loaded_count"] else "empty"))
            if status not in ("succeeded", "empty", "failed", "cancelled"):
                raise ValueError("搜索完成状态无效")
            if batch["search_status"] == "cancelled" and status != "cancelled":
                raise ValueError("搜索批次已取消")
            if status == "succeeded" and not batch["loaded_count"]:
                status = "empty"
            if status == "empty" and batch["loaded_count"]:
                raise ValueError("已有岗位的批次不能标记为空结果")
            batch["search_status"] = status
            connection.execute(text("UPDATE career_assistant.job_search_batches SET search_status=:status,updated_at=NOW() WHERE id=:id"), {"id": batch_id, "status": status})
            return self._receipt(connection, batch, request, "complete", {})

    def set_analysis_turn(self, actor_id, conversation_id, batch_id, turn_id):
        with self._database.transaction() as connection:
            self._conversation(connection, actor_id, conversation_id, write=True)
            self._batch(connection, conversation_id, batch_id, lock=True)
            connection.execute(text("UPDATE career_assistant.job_search_batches SET analysis_turn_id=:turn_id WHERE id=:id"), {"id": batch_id, "turn_id": turn_id})
            return self._public(self._batch(connection, conversation_id, batch_id))

    def get_jobs(self, actor_id, conversation_id, batch_id, *, revision=None, cursor=0, limit=50):
        if type(cursor) is not int or cursor < 0 or type(limit) is not int or not 1 <= limit <= 50:
            raise ValueError("岗位分页参数无效")
        with self._database.transaction() as connection:
            self._conversation(connection, actor_id, conversation_id)
            batch = self._batch(connection, conversation_id, batch_id)
            version = batch["revision"] if revision is None else revision
            self._validate_revision(version, batch["revision"])
            jobs = self._jobs(connection, batch_id, version, cursor=cursor, limit=limit + 1)
            return {"batch": {**self._public(batch), "revision": version}, "jobs": jobs[:limit],
                    "next_cursor": cursor + limit if len(jobs) > limit else None}

    @staticmethod
    def _validate_revision(value, maximum):
        if type(value) is not int or not 0 <= value <= maximum:
            raise ValueError("搜索版本不存在")

    def capture_snapshot(self, actor_id, conversation_id, view=None, *, connection=None):
        with (nullcontext(connection) if connection is not None else self._database.transaction()) as connection:
            self._conversation(connection, actor_id, conversation_id, write=True)
            self._import_legacy(connection, conversation_id)
            view = view or {}
            if not isinstance(view, dict):
                raise ValueError("搜索视图格式无效")
            batches = self._catalog(connection, conversation_id)
            revisions = view.get("revisions") or {}
            if not isinstance(revisions, dict) or any(key not in {batch["id"] for batch in batches} for key in revisions):
                raise ValueError("搜索版本引用无效")
            catalog = []
            for batch in batches:
                version = revisions.get(batch["id"], batch["revision"])
                self._validate_revision(version, batch["revision"])
                # 数量也按引用版本计算，不能把晚到分页的数量带入旧快照。
                count = connection.execute(text("""SELECT COUNT(DISTINCT job_ref)
                    FROM career_assistant.job_search_job_versions WHERE batch_id=:id AND revision<=:revision"""),
                    {"id": batch["id"], "revision": version}).scalar_one()
                catalog.append({key: batch[key] for key in ("id", "batch_no", "source_message_id", "conditions", "search_status", "has_more", "legacy_imported", "legacy_total_count")}
                               | {"batch_id": batch["id"], "revision": version, "loaded_count": count})
            active = view.get("active_batch_id")
            if active is None and batches:
                focused = [batch for batch in batches if batch.get("last_focused_at")]
                active = max(focused, key=lambda batch: batch["last_focused_at"])["id"] if focused else batches[-1]["id"]
            lookup = {batch["id"]: batch for batch in batches}
            if active is not None and active not in lookup:
                raise ValueError("活动搜索批次不存在")
            focus = view.get("focused_job_ref", lookup.get(active, {}).get("focused_job_ref"))
            if focus:
                entry = next(item for item in catalog if item["id"] == active)
                exists = connection.execute(text("""SELECT EXISTS(SELECT 1
                    FROM career_assistant.job_search_job_versions
                    WHERE batch_id=:id AND revision<=:revision AND job_ref=:ref)"""),
                    {"id": active, "revision": entry["revision"], "ref": focus}).scalar_one()
                if not exists:
                    raise ValueError("当前预览尚未保存到引用版本")
            dirty = view.get("uncommitted_batch_ids", [])
            if not isinstance(dirty, list) or any(item not in lookup for item in dirty):
                raise ValueError("未保存批次引用无效")
            return {"schema_version": 1, "catalog": catalog, "active_batch_id": active,
                    "focused_job_ref": focus, "uncommitted_batch_ids": dirty}

    def resolve_snapshot(self, actor_id, conversation_id, snapshot):
        if not isinstance(snapshot, dict) or snapshot.get("schema_version") != 1 or not isinstance(snapshot.get("catalog"), list):
            raise ValueError("搜索快照格式无效")
        with self._database.transaction() as connection:
            self._conversation(connection, actor_id, conversation_id)
            batches = []
            for entry in snapshot.get("catalog", []):
                batch_id = entry.get("batch_id", entry.get("id"))
                actual = self._batch(connection, conversation_id, batch_id)
                self._validate_revision(entry["revision"], actual["revision"])
                batches.append({**entry, "id": str(batch_id), "jobs": self._jobs(connection, batch_id, entry["revision"])})
            return {**snapshot, "batches": batches}
