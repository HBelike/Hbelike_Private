"""长期求职记忆的六类白名单、校验和用户纠正规则。"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from enum import StrEnum
from uuid import UUID


class CareerMemoryType(StrEnum):
    JOB_INTENTION = "job_intention"
    WORK_EXPERIENCE = "work_experience"
    EDUCATION = "education"
    AWARD = "award"
    PUBLICATION = "publication"
    PERSONAL_ADVANTAGE = "personal_advantage"


class CareerMemoryStatus(StrEnum):
    CANDIDATE = "candidate"
    ACTIVE = "active"
    DISABLED = "disabled"
    SUPERSEDED = "superseded"


MEMORY_SOURCE_KINDS = frozenset(
    {
        "explicit_user_statement",
        "explicit_user_correction",
        "confirmed_resume",
        "user_confirmed_candidate",
    },
)

_ALLOWED_VALUE_KEYS = {
    CareerMemoryType.JOB_INTENTION: frozenset({"statement", "role", "location", "industry"}),
    CareerMemoryType.WORK_EXPERIENCE: frozenset({"summary", "company", "role", "period"}),
    CareerMemoryType.EDUCATION: frozenset({"summary", "school", "degree", "major", "period"}),
    CareerMemoryType.AWARD: frozenset({"summary", "name", "issuer", "date"}),
    CareerMemoryType.PUBLICATION: frozenset({"summary", "title", "venue", "date"}),
    CareerMemoryType.PERSONAL_ADVANTAGE: frozenset({"summary", "skill", "evidence"}),
}


@dataclass(frozen=True)
class CareerMemoryDraft:
    memory_type: CareerMemoryType
    normalized_value: dict[str, object]
    display_text: str
    source_kind: str
    career_space_id: UUID | None = None
    source_message_id: UUID | None = None
    source_conversation_id: UUID | None = None
    candidate_profile_id: UUID | None = None
    candidate_profile_version: int | None = None

    def validate(self) -> "CareerMemoryDraft":
        display_text = " ".join(self.display_text.split())
        if not display_text or len(display_text) > 500:
            raise ValueError("求职记忆正文长度必须在 1 到 500 字符之间")
        if self.source_kind not in MEMORY_SOURCE_KINDS:
            raise ValueError("求职记忆来源不可信")
        if self.memory_type is CareerMemoryType.JOB_INTENTION and self.career_space_id is None:
            raise ValueError("岗位意向必须归属职业空间")
        unknown = set(self.normalized_value) - _ALLOWED_VALUE_KEYS[self.memory_type]
        if unknown:
            raise ValueError(f"规范化值包含未知字段：{', '.join(sorted(unknown))}")
        encoded = json.dumps(self.normalized_value, ensure_ascii=False, separators=(",", ":"))
        if len(encoded) > 2_000:
            raise ValueError("求职记忆规范化值过长")
        if self.candidate_profile_version is not None and self.candidate_profile_version < 1:
            raise ValueError("简历版本必须大于 0")
        return replace(self, display_text=display_text)
