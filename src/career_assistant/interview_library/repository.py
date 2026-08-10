"""面经库的 PostgreSQL 仓储。

仓储是唯一允许拼写面经库 SQL 的位置。它只写入 Markdown、元数据、任务状态和切片，
不接触原始附件、Cookie 明文或模型 API Key。
"""

from __future__ import annotations

import json
import math
from datetime import date
from typing import Iterable
from uuid import UUID, uuid4

from sqlalchemy import RowMapping, text

from src.career_assistant.interview_library.chunking import CHUNKING_VERSION, ChunkDraft
from src.career_assistant.interview_library.models import (
    CollectionCandidateStatus,
    CollectionConnectorKind,
    CollectionJobStatus,
    IngestionJobStatus,
    IngestionTriggerType,
    InterviewCollectionCandidateRecord,
    InterviewCollectionJobRecord,
    InterviewChunkRecord,
    InterviewChunkCandidate,
    InterviewCompanyRecord,
    InterviewExperienceRecord,
    InterviewExperienceStatus,
    InterviewIngestionJobRecord,
    InterviewSourceType,
)
from src.career_assistant.persistence.database import CareerDatabase


class InterviewLibraryRepository:
    """管理公司树、面经正文、切片和入库任务的事务边界。"""

    def __init__(self, database: CareerDatabase) -> None:
        self._database = database

    def upsert_company(
        self,
        organization_id: UUID,
        display_name: str,
        *,
        aliases: Iterable[str] = (),
    ) -> InterviewCompanyRecord:
        """创建或更新面经树根节点；同一组织内按规范化名称去重。"""

        normalized_display_name = self._normalize_text(display_name, "公司名称", 120)
        normalized_name = self._normalize_key(normalized_display_name)
        normalized_aliases = self._normalize_tags(aliases, maximum_items=20, maximum_length=80)
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.interview_companies
                        (id, organization_id, display_name, normalized_name, aliases)
                    VALUES
                        (:id, :organization_id, :display_name, :normalized_name,
                         CAST(:aliases AS jsonb))
                    ON CONFLICT (organization_id, normalized_name) DO UPDATE
                    SET display_name = EXCLUDED.display_name,
                        aliases = EXCLUDED.aliases,
                        updated_at = NOW()
                    RETURNING id, organization_id, display_name, normalized_name, aliases,
                              created_at, updated_at
                    """,
                ),
                {
                    "id": uuid4(),
                    "organization_id": organization_id,
                    "display_name": normalized_display_name,
                    "normalized_name": normalized_name,
                    "aliases": json.dumps(normalized_aliases),
                },
            ).mappings().one()
        return self._to_company(row)

    def create_experience(
        self,
        *,
        organization_id: UUID,
        company_id: UUID,
        job_name: str,
        role_name: str,
        interview_date: date | None,
        source_type: InterviewSourceType,
        markdown_content: str,
        source_content_hash: str,
        normalized_markdown: str | None = None,
        source_platform: str | None = None,
        source_url: str | None = None,
        summary_text: str | None = None,
        tags: Iterable[str] = (),
        status: InterviewExperienceStatus = InterviewExperienceStatus.PARSED,
    ) -> InterviewExperienceRecord:
        """写入一份已解析的面经正文，拒绝无归属公司或空 Markdown。"""

        normalized_job_name = self._normalize_text(job_name, "面经名称", 220)
        normalized_role_name = self._normalize_text(role_name, "岗位名称", 160)
        normalized_content = self._normalize_markdown(markdown_content, "面经 Markdown", 300_000)
        normalized_hash = self._normalize_hash(source_content_hash)
        normalized_summary = self._normalize_optional_text(summary_text, "面经摘要", 12_000)
        normalized_platform = self._normalize_optional_text(source_platform, "来源平台", 80)
        normalized_url = self._normalize_optional_url(source_url)
        normalized_markdown_value = self._normalize_optional_markdown(
            normalized_markdown,
            "规范化 Markdown",
            300_000,
        ) or normalized_content
        normalized_tags = self._normalize_tags(tags, maximum_items=30, maximum_length=60)
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.interview_experiences (
                        id, organization_id, company_id, job_name, role_name,
                        normalized_role_name, interview_date, source_type, source_platform,
                        source_url, source_content_hash, markdown_content, normalized_markdown,
                        summary_text, tags, status
                    )
                    SELECT
                        :id, :organization_id, company.id, :job_name, :role_name,
                        :normalized_role_name, :interview_date, :source_type,
                        :source_platform, :source_url, :source_content_hash,
                        :markdown_content, :normalized_markdown, :summary_text,
                        CAST(:tags AS jsonb), :status
                    FROM career_assistant.interview_companies AS company
                    WHERE company.id = :company_id
                      AND company.organization_id = :organization_id
                    ON CONFLICT (organization_id, company_id, job_name) DO UPDATE
                    SET role_name = EXCLUDED.role_name,
                        normalized_role_name = EXCLUDED.normalized_role_name,
                        interview_date = EXCLUDED.interview_date,
                        source_type = EXCLUDED.source_type,
                        source_platform = EXCLUDED.source_platform,
                        source_url = EXCLUDED.source_url,
                        source_content_hash = EXCLUDED.source_content_hash,
                        markdown_content = EXCLUDED.markdown_content,
                        normalized_markdown = EXCLUDED.normalized_markdown,
                        summary_text = EXCLUDED.summary_text,
                        tags = EXCLUDED.tags,
                        status = 'parsed',
                        failure_code = NULL,
                        failure_message = NULL,
                        chunking_version = NULL,
                        indexed_at = NULL,
                        updated_at = NOW()
                    RETURNING id, organization_id, company_id, job_name, role_name,
                              normalized_role_name, interview_date, source_type,
                              source_platform, source_url, source_content_hash,
                              markdown_content, normalized_markdown, summary_text, tags,
                              status, chunking_version, indexed_at, created_at, updated_at
                    """,
                ),
                {
                    "id": uuid4(),
                    "organization_id": organization_id,
                    "company_id": company_id,
                    "job_name": normalized_job_name,
                    "role_name": normalized_role_name,
                    "normalized_role_name": self._normalize_key(normalized_role_name),
                    "interview_date": interview_date,
                    "source_type": source_type.value,
                    "source_platform": normalized_platform,
                    "source_url": normalized_url,
                    "source_content_hash": normalized_hash,
                    "markdown_content": normalized_content,
                    "normalized_markdown": normalized_markdown_value,
                    "summary_text": normalized_summary,
                    "tags": json.dumps(normalized_tags),
                    "status": status.value,
                },
            ).mappings().one_or_none()
        if row is None:
            raise LookupError("公司不存在或不属于当前组织，不能写入面经")
        return self._to_experience(row, company_name=self._company_name_for(company_id, organization_id))

    def update_experience_markdown(
        self,
        organization_id: UUID,
        experience_id: UUID,
        *,
        markdown_content: str,
        normalized_markdown: str,
        source_content_hash: str,
        summary_text: str | None,
        tags: Iterable[str],
    ) -> InterviewExperienceRecord:
        """保存人工编辑后的 Markdown，并标记为待重新建立索引。"""

        normalized_content = self._normalize_markdown(markdown_content, "面经 Markdown", 300_000)
        normalized_markdown_value = self._normalize_markdown(normalized_markdown, "规范化 Markdown", 300_000)
        normalized_hash = self._normalize_hash(source_content_hash)
        normalized_summary = self._normalize_optional_text(summary_text, "面经摘要", 12_000)
        normalized_tags = self._normalize_tags(tags, maximum_items=30, maximum_length=60)
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    UPDATE career_assistant.interview_experiences AS experience
                    SET markdown_content = :markdown_content,
                        normalized_markdown = :normalized_markdown,
                        source_content_hash = :source_content_hash,
                        summary_text = :summary_text,
                        tags = CAST(:tags AS jsonb),
                        status = 'parsed',
                        chunking_version = NULL,
                        indexed_at = NULL,
                        updated_at = NOW()
                    WHERE experience.id = :experience_id
                      AND experience.organization_id = :organization_id
                    RETURNING experience.id, experience.organization_id, experience.company_id,
                              experience.job_name, experience.role_name,
                              experience.normalized_role_name, experience.interview_date,
                              experience.source_type, experience.source_platform,
                              experience.source_url, experience.source_content_hash,
                              experience.markdown_content, experience.normalized_markdown,
                              experience.summary_text, experience.tags, experience.status,
                              experience.chunking_version, experience.indexed_at,
                              experience.created_at, experience.updated_at
                    """,
                ),
                {
                    "experience_id": experience_id,
                    "organization_id": organization_id,
                    "markdown_content": normalized_content,
                    "normalized_markdown": normalized_markdown_value,
                    "source_content_hash": normalized_hash,
                    "summary_text": normalized_summary,
                    "tags": json.dumps(normalized_tags),
                },
            ).mappings().one_or_none()
        if row is None:
            raise LookupError("面经不存在或无访问权限")
        return self._to_experience(row, company_name=self._company_name_for(row["company_id"], organization_id))

    def replace_chunks(
        self,
        organization_id: UUID,
        experience_id: UUID,
        chunks: Iterable[ChunkDraft],
        *,
        embedding_model: str | None = None,
    ) -> list[InterviewChunkRecord]:
        """原子替换一份面经的文本切片；向量由后续 Embedding Job 回填。"""

        normalized_chunks = list(chunks)
        if not normalized_chunks:
            raise ValueError("至少需要一条面经切片")
        if len(normalized_chunks) > 2_000:
            raise ValueError("单份面经切片数量不能超过 2000")
        chunking_version = CHUNKING_VERSION
        normalized_embedding_model = self._normalize_optional_text(embedding_model, "Embedding 模型", 200)
        with self._database.transaction() as connection:
            exists = connection.execute(
                text(
                    """
                    SELECT 1
                    FROM career_assistant.interview_experiences
                    WHERE id = :experience_id
                      AND organization_id = :organization_id
                    FOR UPDATE
                    """,
                ),
                {"experience_id": experience_id, "organization_id": organization_id},
            ).scalar_one_or_none()
            if exists is None:
                raise LookupError("面经不存在或无访问权限")
            connection.execute(
                text("DELETE FROM career_assistant.interview_chunks WHERE experience_id = :experience_id"),
                {"experience_id": experience_id},
            )
            inserted_rows: list[RowMapping] = []
            for chunk in normalized_chunks:
                row = connection.execute(
                    text(
                        """
                        INSERT INTO career_assistant.interview_chunks (
                            id, experience_id, parent_heading, heading_path, chunk_index,
                            content_text, contextual_content, token_estimate, chunk_hash,
                            chunking_version, embedding_model
                        ) VALUES (
                            :id, :experience_id, :parent_heading, :heading_path,
                            :chunk_index, :content_text, :contextual_content,
                            :token_estimate, :chunk_hash, :chunking_version, :embedding_model
                        )
                        RETURNING id, experience_id, parent_heading, heading_path, chunk_index,
                                  content_text, contextual_content, token_estimate, chunk_hash,
                                  chunking_version, embedding_model, created_at, updated_at
                        """,
                    ),
                    {
                        "id": uuid4(),
                        "experience_id": experience_id,
                        "parent_heading": chunk.parent_heading,
                        "heading_path": chunk.heading_path,
                        "chunk_index": chunk.chunk_index,
                        "content_text": chunk.content_text,
                        "contextual_content": chunk.contextual_content,
                        "token_estimate": chunk.token_estimate,
                        "chunk_hash": chunk.chunk_hash,
                        "chunking_version": chunking_version,
                        "embedding_model": normalized_embedding_model,
                    },
                ).mappings().one()
                inserted_rows.append(row)
            connection.execute(
                text(
                    """
                    UPDATE career_assistant.interview_experiences
                    SET status = 'indexed',
                        chunking_version = :chunking_version,
                        indexed_at = NOW(),
                        updated_at = NOW()
                    WHERE id = :experience_id
                    """,
                ),
                {"experience_id": experience_id, "chunking_version": chunking_version},
            )
        return [self._to_chunk(row) for row in inserted_rows]

    def list_chunks(
        self,
        organization_id: UUID,
        experience_id: UUID,
    ) -> list[InterviewChunkRecord]:
        """读取一份面经的现有切片，供异步或同步向量索引器重建 embedding。

        切片正文已经是可持久化的 Markdown 派生文本，因此该方法不访问也不恢复任何
        原始附件；多个并发索引任务只会读取同一稳定快照。
        """

        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT chunk.id, chunk.experience_id, chunk.parent_heading,
                           chunk.heading_path, chunk.chunk_index, chunk.content_text,
                           chunk.contextual_content, chunk.token_estimate, chunk.chunk_hash,
                           chunk.chunking_version, chunk.embedding_model,
                           chunk.created_at, chunk.updated_at
                    FROM career_assistant.interview_chunks AS chunk
                    INNER JOIN career_assistant.interview_experiences AS experience
                        ON experience.id = chunk.experience_id
                    WHERE chunk.experience_id = :experience_id
                      AND experience.organization_id = :organization_id
                    ORDER BY chunk.chunk_index ASC
                    """,
                ),
                {
                    "organization_id": organization_id,
                    "experience_id": experience_id,
                },
            ).mappings().all()
        return [self._to_chunk(row) for row in rows]

    def replace_chunk_embeddings(
        self,
        organization_id: UUID,
        *,
        embedding_model: str,
        embeddings_by_chunk_id: dict[UUID, list[float]],
        expected_dimensions: int,
    ) -> int:
        """事务性写入一批向量，并验证每条向量维度与数值边界。

        ``pgvector`` 不接受维度不一致的数组。先在应用层拦截错误，避免一批索引任务
        因单个上游模型异常输出而留下部分写入；数据库事务会在任一失败时整体回滚。
        """

        normalized_model = self._normalize_text(embedding_model, "Embedding 模型", 200)
        if not embeddings_by_chunk_id:
            return 0
        if not 1 <= expected_dimensions <= 8_192:
            raise ValueError("Embedding 维度必须在 1 到 8192 之间")

        prepared_embeddings: list[tuple[UUID, str]] = []
        for chunk_id, vector in embeddings_by_chunk_id.items():
            if len(vector) != expected_dimensions:
                raise ValueError(
                    f"Embedding 维度不匹配：期望 {expected_dimensions}，实际 {len(vector)}",
                )
            if not all(isinstance(item, int | float) and math.isfinite(float(item)) for item in vector):
                raise ValueError("Embedding 包含非法数值")
            vector_literal = "[" + ",".join(f"{float(item):.9g}" for item in vector) + "]"
            prepared_embeddings.append((chunk_id, vector_literal))

        updated_count = 0
        with self._database.transaction() as connection:
            for chunk_id, vector_literal in prepared_embeddings:
                result = connection.execute(
                    text(
                        """
                        UPDATE career_assistant.interview_chunks AS chunk
                        SET embedding = CAST(:embedding AS vector),
                            embedding_model = :embedding_model,
                            updated_at = NOW()
                        FROM career_assistant.interview_experiences AS experience
                        WHERE chunk.id = :chunk_id
                          AND experience.id = chunk.experience_id
                          AND experience.organization_id = :organization_id
                        """,
                    ),
                    {
                        "chunk_id": chunk_id,
                        "embedding": vector_literal,
                        "embedding_model": normalized_model,
                        "organization_id": organization_id,
                    },
                )
                updated_count += result.rowcount
        return updated_count

    def search_lexical_chunks(
        self,
        organization_id: UUID,
        query: str,
        *,
        limit: int = 24,
        experience_ids: tuple[UUID, ...] = (),
    ) -> list[InterviewChunkCandidate]:
        """用 PostgreSQL trigram 做无需模型凭证的中文关键词兜底召回。

        该路径对公司名、岗位、标题和正文同时检索。向量服务未配置、限流或维护时，
        面经库仍然可用，是上线后降级策略的一部分。
        """

        normalized_query = self._normalize_text(query, "检索关键词", 240).lower()
        if not 1 <= limit <= 100:
            raise ValueError("检索数量必须在 1 到 100 之间")
        scope_clause, scope_parameters = self._experience_scope_filter(experience_ids)
        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    f"""
                    SELECT chunk.id, chunk.experience_id, chunk.parent_heading,
                           chunk.heading_path, chunk.chunk_index, chunk.content_text,
                           chunk.contextual_content, chunk.token_estimate, chunk.chunk_hash,
                           chunk.chunking_version, chunk.embedding_model,
                           chunk.created_at, chunk.updated_at,
                           company.display_name AS company_name,
                           experience.job_name, experience.role_name,
                           experience.interview_date, experience.source_url,
                           GREATEST(
                               similarity(LOWER(chunk.contextual_content), :query),
                               similarity(LOWER(company.display_name), :query),
                               similarity(LOWER(experience.job_name), :query),
                               similarity(LOWER(experience.role_name), :query)
                           ) AS lexical_score
                    FROM career_assistant.interview_chunks AS chunk
                    INNER JOIN career_assistant.interview_experiences AS experience
                        ON experience.id = chunk.experience_id
                    INNER JOIN career_assistant.interview_companies AS company
                        ON company.id = experience.company_id
                    WHERE experience.organization_id = :organization_id
                      {scope_clause}
                      AND (
                          LOWER(chunk.contextual_content) LIKE :like_query
                          OR LOWER(company.display_name) LIKE :like_query
                          OR LOWER(experience.job_name) LIKE :like_query
                          OR LOWER(experience.role_name) LIKE :like_query
                          OR similarity(LOWER(chunk.contextual_content), :query) >= 0.06
                          OR similarity(LOWER(experience.job_name), :query) >= 0.06
                      )
                    ORDER BY lexical_score DESC, chunk.updated_at DESC
                    LIMIT :limit
                    """,
                ),
                {
                    "organization_id": organization_id,
                    "query": normalized_query,
                    "like_query": f"%{normalized_query}%",
                    "limit": limit,
                    **scope_parameters,
                },
            ).mappings().all()
        return [self._to_candidate(row, lexical_score=float(row["lexical_score"])) for row in rows]

    def search_semantic_chunks(
        self,
        organization_id: UUID,
        query_embedding: list[float],
        *,
        expected_dimensions: int,
        limit: int = 24,
        experience_ids: tuple[UUID, ...] = (),
    ) -> list[InterviewChunkCandidate]:
        """在已完成向量化的切片中执行余弦近邻召回。"""

        if len(query_embedding) != expected_dimensions:
            raise ValueError("查询向量维度与面经索引不一致")
        if not all(isinstance(item, int | float) and math.isfinite(float(item)) for item in query_embedding):
            raise ValueError("查询向量包含非法数值")
        if not 1 <= limit <= 100:
            raise ValueError("检索数量必须在 1 到 100 之间")
        vector_literal = "[" + ",".join(f"{float(item):.9g}" for item in query_embedding) + "]"
        scope_clause, scope_parameters = self._experience_scope_filter(experience_ids)
        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    f"""
                    SELECT chunk.id, chunk.experience_id, chunk.parent_heading,
                           chunk.heading_path, chunk.chunk_index, chunk.content_text,
                           chunk.contextual_content, chunk.token_estimate, chunk.chunk_hash,
                           chunk.chunking_version, chunk.embedding_model,
                           chunk.created_at, chunk.updated_at,
                           company.display_name AS company_name,
                           experience.job_name, experience.role_name,
                           experience.interview_date, experience.source_url,
                           (1 - (chunk.embedding <=> CAST(:query_embedding AS vector)))
                               AS semantic_score
                    FROM career_assistant.interview_chunks AS chunk
                    INNER JOIN career_assistant.interview_experiences AS experience
                        ON experience.id = chunk.experience_id
                    INNER JOIN career_assistant.interview_companies AS company
                        ON company.id = experience.company_id
                    WHERE experience.organization_id = :organization_id
                      AND chunk.embedding IS NOT NULL
                      {scope_clause}
                    ORDER BY chunk.embedding <=> CAST(:query_embedding AS vector) ASC,
                             chunk.updated_at DESC
                    LIMIT :limit
                    """,
                ),
                {
                    "organization_id": organization_id,
                    "query_embedding": vector_literal,
                    "limit": limit,
                    **scope_parameters,
                },
            ).mappings().all()
        return [self._to_candidate(row, semantic_score=float(row["semantic_score"])) for row in rows]

    def list_chunks_for_experiences(
        self,
        organization_id: UUID,
        experience_ids: tuple[UUID, ...],
        *,
        limit: int = 12,
    ) -> list[InterviewChunkCandidate]:
        """为用户显式选择的面经提供稳定兜底证据。

        当关键词或向量召回均未命中时，不能因为标题写法差异而让 ``@面经`` 失效。
        此方法只返回已授权资料的前若干结构化切片，不参与全库检索排序。
        """

        if not experience_ids:
            return []
        if not 1 <= limit <= 100:
            raise ValueError("检索数量必须在 1 到 100 之间")
        scope_clause, scope_parameters = self._experience_scope_filter(experience_ids)
        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    f"""
                    SELECT chunk.id, chunk.experience_id, chunk.parent_heading,
                           chunk.heading_path, chunk.chunk_index, chunk.content_text,
                           chunk.contextual_content, chunk.token_estimate, chunk.chunk_hash,
                           chunk.chunking_version, chunk.embedding_model,
                           chunk.created_at, chunk.updated_at,
                           company.display_name AS company_name,
                           experience.job_name, experience.role_name,
                           experience.interview_date, experience.source_url
                    FROM career_assistant.interview_chunks AS chunk
                    INNER JOIN career_assistant.interview_experiences AS experience
                        ON experience.id = chunk.experience_id
                    INNER JOIN career_assistant.interview_companies AS company
                        ON company.id = experience.company_id
                    WHERE experience.organization_id = :organization_id
                      {scope_clause}
                    ORDER BY experience.updated_at DESC, chunk.chunk_index ASC
                    LIMIT :limit
                    """,
                ),
                {
                    "organization_id": organization_id,
                    "limit": limit,
                    **scope_parameters,
                },
            ).mappings().all()
        return [self._to_candidate(row) for row in rows]

    def create_ingestion_job(
        self,
        *,
        organization_id: UUID,
        trigger_type: IngestionTriggerType,
        source_url: str | None = None,
        source_platform: str | None = None,
    ) -> InterviewIngestionJobRecord:
        """为上传、URL 或未来扫描器创建可追踪任务；不带原始文件内容。"""

        normalized_url = self._normalize_optional_url(source_url)
        normalized_platform = self._normalize_optional_text(source_platform, "来源平台", 80)
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.interview_ingestion_jobs (
                        id, organization_id, trigger_type, source_url, source_platform
                    ) VALUES (
                        :id, :organization_id, :trigger_type, :source_url, :source_platform
                    )
                    RETURNING id, organization_id, experience_id, trigger_type, source_url,
                              source_platform, status, attempt_count, error_code,
                              error_message, started_at, completed_at, created_at, updated_at
                    """,
                ),
                {
                    "id": uuid4(),
                    "organization_id": organization_id,
                    "trigger_type": trigger_type.value,
                    "source_url": normalized_url,
                    "source_platform": normalized_platform,
                },
            ).mappings().one()
        return self._to_ingestion_job(row)

    def create_collection_job(
        self,
        *,
        organization_id: UUID,
        platform_key: str,
        keyword: str,
        requested_limit: int,
        connector_kind: CollectionConnectorKind,
        policy_decision: str,
    ) -> InterviewCollectionJobRecord:
        """创建资料发现任务，不执行外部抓取也不保存任何第三方登录凭证。"""

        normalized_platform = self._normalize_text(platform_key, "平台标识", 50).lower()
        normalized_keyword = self._normalize_text(keyword, "检索关键词", 180)
        normalized_policy = self._normalize_text(policy_decision, "采集策略说明", 500)
        if not 1 <= requested_limit <= 50:
            raise ValueError("候选资料数量必须在 1 到 50 之间")

        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.interview_collection_jobs (
                        id, organization_id, platform_key, keyword, requested_limit,
                        connector_kind, policy_decision
                    ) VALUES (
                        :id, :organization_id, :platform_key, :keyword, :requested_limit,
                        :connector_kind, :policy_decision
                    )
                    RETURNING id, organization_id, platform_key, keyword, requested_limit,
                              connector_kind, status, policy_decision, error_code,
                              error_message, started_at, completed_at, created_at, updated_at
                    """,
                ),
                {
                    "id": uuid4(),
                    "organization_id": organization_id,
                    "platform_key": normalized_platform,
                    "keyword": normalized_keyword,
                    "requested_limit": requested_limit,
                    "connector_kind": connector_kind.value,
                    "policy_decision": normalized_policy,
                },
            ).mappings().one()
        return self._to_collection_job(row)

    def get_collection_job(
        self,
        organization_id: UUID,
        job_id: UUID,
    ) -> InterviewCollectionJobRecord | None:
        """读取单个采集任务，强制校验组织边界。"""

        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT id, organization_id, platform_key, keyword, requested_limit,
                           connector_kind, status, policy_decision, error_code,
                           error_message, started_at, completed_at, created_at, updated_at
                    FROM career_assistant.interview_collection_jobs
                    WHERE id = :job_id AND organization_id = :organization_id
                    """,
                ),
                {"job_id": job_id, "organization_id": organization_id},
            ).mappings().one_or_none()
        return self._to_collection_job(row) if row is not None else None

    def update_collection_job_status(
        self,
        organization_id: UUID,
        job_id: UUID,
        *,
        status: CollectionJobStatus,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> InterviewCollectionJobRecord:
        """原子更新采集任务状态，并在结束状态记录完成时间。"""

        normalized_code = self._normalize_optional_text(error_code, "错误代码", 80)
        normalized_message = self._normalize_optional_text(error_message, "错误说明", 1_000)
        terminal = status in {
            CollectionJobStatus.SUCCEEDED,
            CollectionJobStatus.FAILED,
            CollectionJobStatus.CANCELLED,
        }
        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    UPDATE career_assistant.interview_collection_jobs
                    SET status = :status,
                        error_code = :error_code,
                        error_message = :error_message,
                        started_at = CASE
                            WHEN :status = 'running' AND started_at IS NULL THEN NOW()
                            ELSE started_at
                        END,
                        completed_at = CASE WHEN :terminal THEN NOW() ELSE NULL END,
                        updated_at = NOW()
                    WHERE id = :job_id AND organization_id = :organization_id
                    RETURNING id, organization_id, platform_key, keyword, requested_limit,
                              connector_kind, status, policy_decision, error_code,
                              error_message, started_at, completed_at, created_at, updated_at
                    """,
                ),
                {
                    "job_id": job_id,
                    "organization_id": organization_id,
                    "status": status.value,
                    "error_code": normalized_code,
                    "error_message": normalized_message,
                    "terminal": terminal,
                },
            ).mappings().one_or_none()
        if row is None:
            raise LookupError("采集任务不存在或无访问权限")
        return self._to_collection_job(row)

    def create_collection_candidate(
        self,
        organization_id: UUID,
        *,
        collection_job_id: UUID,
        source_url: str,
        canonical_url: str | None = None,
        source_platform: str,
        title: str | None = None,
        snippet: str | None = None,
        extracted_markdown: str | None = None,
        content_hash: str | None = None,
        status: CollectionCandidateStatus = CollectionCandidateStatus.DISCOVERED,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> InterviewCollectionCandidateRecord:
        """保存候选的必要文本与元数据，拒绝写入原始 HTML 或会话凭证。"""

        normalized_source_url = self._normalize_optional_url(source_url)
        normalized_canonical_url = self._normalize_optional_url(canonical_url or source_url)
        if normalized_source_url is None or normalized_canonical_url is None:
            raise ValueError("候选资料必须提供有效的 http 或 https 地址")
        normalized_platform = self._normalize_text(source_platform, "来源平台", 80)
        normalized_title = self._normalize_optional_text(title, "候选标题", 300)
        normalized_snippet = self._normalize_optional_text(snippet, "候选摘要", 4_000)
        normalized_markdown = self._normalize_optional_markdown(
            extracted_markdown,
            "候选正文",
            300_000,
        )
        normalized_hash = self._normalize_hash(content_hash) if content_hash else None

        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    INSERT INTO career_assistant.interview_collection_candidates (
                        id, collection_job_id, source_url, canonical_url, source_platform,
                        title, snippet, extracted_markdown, content_hash, status,
                        error_code, error_message
                    )
                    SELECT :id, :collection_job_id, :source_url, :canonical_url,
                           :source_platform, :title, :snippet, :extracted_markdown,
                           :content_hash, :status, :error_code, :error_message
                    WHERE EXISTS (
                        SELECT 1 FROM career_assistant.interview_collection_jobs
                        WHERE id = :collection_job_id AND organization_id = :organization_id
                    )
                    ON CONFLICT (collection_job_id, canonical_url) DO UPDATE
                    SET source_url = EXCLUDED.source_url,
                        source_platform = EXCLUDED.source_platform,
                        title = COALESCE(EXCLUDED.title, interview_collection_candidates.title),
                        snippet = COALESCE(EXCLUDED.snippet, interview_collection_candidates.snippet),
                        extracted_markdown = COALESCE(
                            EXCLUDED.extracted_markdown,
                            interview_collection_candidates.extracted_markdown
                        ),
                        content_hash = COALESCE(
                            EXCLUDED.content_hash,
                            interview_collection_candidates.content_hash
                        ),
                        status = EXCLUDED.status,
                        error_code = EXCLUDED.error_code,
                        error_message = EXCLUDED.error_message,
                        updated_at = NOW()
                    RETURNING id, collection_job_id, source_url, canonical_url, source_platform,
                              title, snippet, published_at, extracted_markdown, content_hash,
                              status, error_code, error_message, created_at, updated_at
                    """,
                ),
                {
                    "id": uuid4(),
                    "organization_id": organization_id,
                    "collection_job_id": collection_job_id,
                    "source_url": normalized_source_url,
                    "canonical_url": normalized_canonical_url,
                    "source_platform": normalized_platform,
                    "title": normalized_title,
                    "snippet": normalized_snippet,
                    "extracted_markdown": normalized_markdown,
                    "content_hash": normalized_hash,
                    "status": status.value,
                    "error_code": self._normalize_optional_text(error_code, "错误代码", 80),
                    "error_message": self._normalize_optional_text(error_message, "错误说明", 1_000),
                },
            ).mappings().one_or_none()
        if row is None:
            raise LookupError("采集任务不存在或无访问权限")
        return self._to_collection_candidate(row)

    def list_collection_candidates(
        self,
        organization_id: UUID,
        collection_job_id: UUID,
    ) -> list[InterviewCollectionCandidateRecord]:
        """列出任务候选项，读取时再次用组织边界隔离。"""

        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT candidate.id, candidate.collection_job_id, candidate.source_url,
                           candidate.canonical_url, candidate.source_platform, candidate.title,
                           candidate.snippet, candidate.published_at,
                           candidate.extracted_markdown, candidate.content_hash,
                           candidate.status, candidate.error_code, candidate.error_message,
                           candidate.created_at, candidate.updated_at
                    FROM career_assistant.interview_collection_candidates AS candidate
                    INNER JOIN career_assistant.interview_collection_jobs AS job
                        ON job.id = candidate.collection_job_id
                    WHERE candidate.collection_job_id = :collection_job_id
                      AND job.organization_id = :organization_id
                    ORDER BY candidate.created_at ASC
                    """,
                ),
                {
                    "collection_job_id": collection_job_id,
                    "organization_id": organization_id,
                },
            ).mappings().all()
        return [self._to_collection_candidate(row) for row in rows]

    def get_collection_candidate(
        self,
        organization_id: UUID,
        candidate_id: UUID,
    ) -> InterviewCollectionCandidateRecord | None:
        """读取一个候选正文，为“选择后入库”重新校验归属。"""

        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT candidate.id, candidate.collection_job_id, candidate.source_url,
                           candidate.canonical_url, candidate.source_platform, candidate.title,
                           candidate.snippet, candidate.published_at,
                           candidate.extracted_markdown, candidate.content_hash,
                           candidate.status, candidate.error_code, candidate.error_message,
                           candidate.created_at, candidate.updated_at
                    FROM career_assistant.interview_collection_candidates AS candidate
                    INNER JOIN career_assistant.interview_collection_jobs AS job
                        ON job.id = candidate.collection_job_id
                    WHERE candidate.id = :candidate_id
                      AND job.organization_id = :organization_id
                    """,
                ),
                {"candidate_id": candidate_id, "organization_id": organization_id},
            ).mappings().one_or_none()
        return self._to_collection_candidate(row) if row is not None else None

    def set_collection_candidate_status(
        self,
        organization_id: UUID,
        candidate_id: UUID,
        *,
        status: CollectionCandidateStatus,
    ) -> InterviewCollectionCandidateRecord:
        """将候选标记为已选择或已入库，防止前端直接篡改其他组织资料。"""

        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    UPDATE career_assistant.interview_collection_candidates AS candidate
                    SET status = :status, updated_at = NOW()
                    FROM career_assistant.interview_collection_jobs AS job
                    WHERE candidate.id = :candidate_id
                      AND job.id = candidate.collection_job_id
                      AND job.organization_id = :organization_id
                    RETURNING candidate.id, candidate.collection_job_id, candidate.source_url,
                              candidate.canonical_url, candidate.source_platform,
                              candidate.title, candidate.snippet, candidate.published_at,
                              candidate.extracted_markdown, candidate.content_hash,
                              candidate.status, candidate.error_code, candidate.error_message,
                              candidate.created_at, candidate.updated_at
                    """,
                ),
                {
                    "candidate_id": candidate_id,
                    "organization_id": organization_id,
                    "status": status.value,
                },
            ).mappings().one_or_none()
        if row is None:
            raise LookupError("候选资料不存在或无访问权限")
        return self._to_collection_candidate(row)

    def complete_ingestion_job(
        self,
        organization_id: UUID,
        job_id: UUID,
        experience_id: UUID,
    ) -> InterviewIngestionJobRecord:
        """将成功入库任务绑定到面经并收口为 succeeded。"""

        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    UPDATE career_assistant.interview_ingestion_jobs AS job
                    SET experience_id = experience.id,
                        status = 'succeeded',
                        attempt_count = job.attempt_count + 1,
                        completed_at = NOW(),
                        updated_at = NOW(),
                        error_code = NULL,
                        error_message = NULL
                    FROM career_assistant.interview_experiences AS experience
                    WHERE job.id = :job_id
                      AND job.organization_id = :organization_id
                      AND experience.id = :experience_id
                      AND experience.organization_id = :organization_id
                    RETURNING job.id, job.organization_id, job.experience_id,
                              job.trigger_type, job.source_url, job.source_platform,
                              job.status, job.attempt_count, job.error_code,
                              job.error_message, job.started_at, job.completed_at,
                              job.created_at, job.updated_at
                    """,
                ),
                {"job_id": job_id, "organization_id": organization_id, "experience_id": experience_id},
            ).mappings().one_or_none()
        if row is None:
            raise LookupError("入库任务或面经不存在，无法收口")
        return self._to_ingestion_job(row)

    def list_tree(self, organization_id: UUID, *, query: str | None = None) -> list[dict[str, object]]:
        """读取公司→岗位→日期的树数据，供 Element Plus Tree 直接消费。"""

        normalized_query = (query or "").strip().lower()
        parameters: dict[str, object] = {"organization_id": organization_id}
        filter_clause = ""
        if normalized_query:
            parameters["query"] = f"%{normalized_query}%"
            filter_clause = """
                AND (
                    LOWER(company.display_name) LIKE :query
                    OR LOWER(experience.job_name) LIKE :query
                    OR LOWER(experience.role_name) LIKE :query
                )
            """
        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    f"""
                    SELECT company.id AS company_id, company.display_name AS company_name,
                           experience.id AS experience_id, experience.job_name,
                           experience.role_name, experience.interview_date,
                           experience.status, experience.updated_at
                    FROM career_assistant.interview_companies AS company
                    INNER JOIN career_assistant.interview_experiences AS experience
                        ON experience.company_id = company.id
                    WHERE company.organization_id = :organization_id
                    {filter_clause}
                    ORDER BY company.display_name ASC,
                             experience.role_name ASC,
                             experience.interview_date DESC NULLS LAST,
                             experience.updated_at DESC
                    """,
                ),
                parameters,
            ).mappings().all()

        companies: dict[UUID, dict[str, object]] = {}
        for row in rows:
            company_id = row["company_id"]
            company = companies.setdefault(
                company_id,
                {
                    "id": f"company:{company_id}",
                    "node_type": "company",
                    "label": row["company_name"],
                    "children": [],
                },
            )
            company["children"].append(
                {
                    "id": str(row["experience_id"]),
                    "node_type": "experience",
                    "label": row["job_name"],
                    "role_name": row["role_name"],
                    "interview_date": row["interview_date"].isoformat() if row["interview_date"] else None,
                    "status": row["status"],
                    "updated_at": row["updated_at"].isoformat(),
                },
            )
        return list(companies.values())

    def get_experience(
        self,
        organization_id: UUID,
        experience_id: UUID,
    ) -> InterviewExperienceRecord | None:
        """读取一份面经 Markdown，用于预览和人工编辑。"""

        with self._database.transaction() as connection:
            row = connection.execute(
                text(
                    """
                    SELECT experience.id, experience.organization_id, experience.company_id,
                           company.display_name AS company_name, experience.job_name,
                           experience.role_name, experience.normalized_role_name,
                           experience.interview_date, experience.source_type,
                           experience.source_platform, experience.source_url,
                           experience.source_content_hash, experience.markdown_content,
                           experience.normalized_markdown, experience.summary_text,
                           experience.tags, experience.status, experience.chunking_version,
                           experience.indexed_at, experience.created_at, experience.updated_at
                    FROM career_assistant.interview_experiences AS experience
                    INNER JOIN career_assistant.interview_companies AS company
                        ON company.id = experience.company_id
                    WHERE experience.id = :experience_id
                      AND experience.organization_id = :organization_id
                    """,
                ),
                {"experience_id": experience_id, "organization_id": organization_id},
            ).mappings().one_or_none()
        return self._to_experience(row) if row is not None else None

    def search_experiences(
        self,
        organization_id: UUID,
        query: str,
        *,
        limit: int = 8,
    ) -> list[InterviewExperienceRecord]:
        """提供 @面经 的轻量名称/公司模糊匹配，RAG 向量召回由下一层实现。"""

        normalized_query = self._normalize_text(query, "检索关键词", 120).lower()
        if not 1 <= limit <= 30:
            raise ValueError("检索数量必须在 1 到 30 之间")
        with self._database.transaction() as connection:
            rows = connection.execute(
                text(
                    """
                    SELECT experience.id, experience.organization_id, experience.company_id,
                           company.display_name AS company_name, experience.job_name,
                           experience.role_name, experience.normalized_role_name,
                           experience.interview_date, experience.source_type,
                           experience.source_platform, experience.source_url,
                           experience.source_content_hash, experience.markdown_content,
                           experience.normalized_markdown, experience.summary_text,
                           experience.tags, experience.status, experience.chunking_version,
                           experience.indexed_at, experience.created_at, experience.updated_at
                    FROM career_assistant.interview_experiences AS experience
                    INNER JOIN career_assistant.interview_companies AS company
                        ON company.id = experience.company_id
                    WHERE experience.organization_id = :organization_id
                      AND (
                        LOWER(company.display_name) LIKE :query
                        OR LOWER(experience.job_name) LIKE :query
                        OR LOWER(experience.role_name) LIKE :query
                      )
                    ORDER BY experience.updated_at DESC
                    LIMIT :limit
                    """,
                ),
                {
                    "organization_id": organization_id,
                    "query": f"%{normalized_query}%",
                    "limit": limit,
                },
            ).mappings().all()
        return [self._to_experience(row) for row in rows]

    def _company_name_for(self, company_id: UUID, organization_id: UUID) -> str:
        with self._database.transaction() as connection:
            value = connection.execute(
                text(
                    """
                    SELECT display_name
                    FROM career_assistant.interview_companies
                    WHERE id = :company_id AND organization_id = :organization_id
                    """,
                ),
                {"company_id": company_id, "organization_id": organization_id},
            ).scalar_one()
        return str(value)

    @staticmethod
    def _normalize_text(value: str, field_name: str, maximum_length: int) -> str:
        normalized = " ".join(value.replace("\x00", "").split()).strip()
        if not normalized:
            raise ValueError(f"{field_name}不能为空")
        if len(normalized) > maximum_length:
            raise ValueError(f"{field_name}不能超过 {maximum_length} 个字符")
        return normalized

    @staticmethod
    def _experience_scope_filter(experience_ids: tuple[UUID, ...]) -> tuple[str, dict[str, object]]:
        """把可选面经范围转换为参数化 SQL，避免拼接用户输入。"""

        normalized_ids = tuple(dict.fromkeys(experience_ids))
        if not normalized_ids:
            return "", {}
        return (
            "AND experience.id = ANY(CAST(:experience_ids AS uuid[]))",
            {"experience_ids": [str(experience_id) for experience_id in normalized_ids]},
        )

    @classmethod
    def _normalize_optional_text(cls, value: str | None, field_name: str, maximum_length: int) -> str | None:
        if value is None or not value.strip():
            return None
        return cls._normalize_text(value, field_name, maximum_length)

    @staticmethod
    def _normalize_markdown(value: str, field_name: str, maximum_length: int) -> str:
        """清除危险空字符但保留 Markdown 换行、列表和表格结构。"""

        normalized = "\n".join(
            line.rstrip()
            for line in value.replace("\r\n", "\n").replace("\r", "\n").replace("\x00", "").split("\n")
        ).strip()
        if not normalized:
            raise ValueError(f"{field_name}不能为空")
        if len(normalized) > maximum_length:
            raise ValueError(f"{field_name}不能超过 {maximum_length} 个字符")
        return normalized

    @classmethod
    def _normalize_optional_markdown(cls, value: str | None, field_name: str, maximum_length: int) -> str | None:
        if value is None or not value.strip():
            return None
        return cls._normalize_markdown(value, field_name, maximum_length)

    @staticmethod
    def _normalize_key(value: str) -> str:
        return "".join(value.lower().split())

    @staticmethod
    def _normalize_hash(value: str) -> str:
        normalized = value.strip().lower()
        if len(normalized) != 64 or any(character not in "0123456789abcdef" for character in normalized):
            raise ValueError("正文摘要指纹必须是 SHA-256 十六进制值")
        return normalized

    @staticmethod
    def _normalize_optional_url(value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        normalized = value.strip()
        if len(normalized) > 2_000 or not normalized.startswith(("http://", "https://")):
            raise ValueError("来源链接必须是长度不超过 2000 的 http 或 https 地址")
        return normalized

    @staticmethod
    def _normalize_tags(values: Iterable[str], *, maximum_items: int, maximum_length: int) -> list[str]:
        result: list[str] = []
        for value in values:
            normalized = " ".join(str(value).split()).strip()
            if not normalized or normalized in result:
                continue
            if len(normalized) > maximum_length:
                raise ValueError("标签长度超过允许范围")
            result.append(normalized)
        if len(result) > maximum_items:
            raise ValueError("标签数量超过允许范围")
        return result

    @staticmethod
    def _to_company(row: RowMapping) -> InterviewCompanyRecord:
        return InterviewCompanyRecord(
            id=row["id"], organization_id=row["organization_id"],
            display_name=row["display_name"], normalized_name=row["normalized_name"],
            aliases=tuple(row["aliases"]), created_at=row["created_at"], updated_at=row["updated_at"],
        )

    @staticmethod
    def _to_experience(row: RowMapping, company_name: str | None = None) -> InterviewExperienceRecord:
        return InterviewExperienceRecord(
            id=row["id"], organization_id=row["organization_id"], company_id=row["company_id"],
            company_name=company_name or row["company_name"], job_name=row["job_name"],
            role_name=row["role_name"], normalized_role_name=row["normalized_role_name"],
            interview_date=row["interview_date"], source_type=InterviewSourceType(row["source_type"]),
            source_platform=row["source_platform"], source_url=row["source_url"],
            summary_text=row["summary_text"], markdown_content=row["markdown_content"],
            normalized_markdown=row["normalized_markdown"], tags=tuple(row["tags"]),
            status=InterviewExperienceStatus(row["status"]), chunking_version=row["chunking_version"],
            indexed_at=row["indexed_at"], created_at=row["created_at"], updated_at=row["updated_at"],
        )

    @staticmethod
    def _to_chunk(row: RowMapping) -> InterviewChunkRecord:
        return InterviewChunkRecord(
            id=row["id"], experience_id=row["experience_id"], parent_heading=row["parent_heading"],
            heading_path=row["heading_path"], chunk_index=row["chunk_index"],
            content_text=row["content_text"], contextual_content=row["contextual_content"],
            token_estimate=row["token_estimate"], chunk_hash=row["chunk_hash"],
            chunking_version=row["chunking_version"], embedding_model=row["embedding_model"],
            created_at=row["created_at"], updated_at=row["updated_at"],
        )

    @classmethod
    def _to_candidate(
        cls,
        row: RowMapping,
        *,
        lexical_score: float | None = None,
        semantic_score: float | None = None,
    ) -> InterviewChunkCandidate:
        """把联表检索结果收敛为不暴露敏感输入的引用候选。"""

        return InterviewChunkCandidate(
            chunk=cls._to_chunk(row),
            company_name=row["company_name"],
            job_name=row["job_name"],
            role_name=row["role_name"],
            interview_date=row["interview_date"],
            source_url=row["source_url"],
            lexical_score=lexical_score,
            semantic_score=semantic_score,
        )

    @staticmethod
    def _to_ingestion_job(row: RowMapping) -> InterviewIngestionJobRecord:
        return InterviewIngestionJobRecord(
            id=row["id"], organization_id=row["organization_id"], experience_id=row["experience_id"],
            trigger_type=IngestionTriggerType(row["trigger_type"]), source_url=row["source_url"],
            source_platform=row["source_platform"], status=IngestionJobStatus(row["status"]),
            attempt_count=row["attempt_count"], error_code=row["error_code"],
            error_message=row["error_message"], started_at=row["started_at"],
            completed_at=row["completed_at"], created_at=row["created_at"], updated_at=row["updated_at"],
        )

    @staticmethod
    def _to_collection_job(row: RowMapping) -> InterviewCollectionJobRecord:
        """把采集任务的数据库行收敛为稳定领域对象。"""

        return InterviewCollectionJobRecord(
            id=row["id"],
            organization_id=row["organization_id"],
            platform_key=row["platform_key"],
            keyword=row["keyword"],
            requested_limit=row["requested_limit"],
            connector_kind=CollectionConnectorKind(row["connector_kind"]),
            status=CollectionJobStatus(row["status"]),
            policy_decision=row["policy_decision"],
            error_code=row["error_code"],
            error_message=row["error_message"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _to_collection_candidate(row: RowMapping) -> InterviewCollectionCandidateRecord:
        """把候选资料的数据库行收敛为不包含原始网页的读取对象。"""

        return InterviewCollectionCandidateRecord(
            id=row["id"],
            collection_job_id=row["collection_job_id"],
            source_url=row["source_url"],
            canonical_url=row["canonical_url"],
            source_platform=row["source_platform"],
            title=row["title"],
            snippet=row["snippet"],
            published_at=row["published_at"],
            extracted_markdown=row["extracted_markdown"],
            content_hash=row["content_hash"],
            status=CollectionCandidateStatus(row["status"]),
            error_code=row["error_code"],
            error_message=row["error_message"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
