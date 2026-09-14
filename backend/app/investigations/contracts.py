from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field, HttpUrl, field_validator, model_validator


class InvestigationType(StrEnum):
    COMPETITIVE_RESEARCH = "competitive_research"


class InvestigationStatus(StrEnum):
    DRAFT = "draft"
    PLANNING = "planning"
    AWAITING_SCOPE_APPROVAL = "awaiting_scope_approval"
    COLLECTING = "collecting"
    NORMALIZING = "normalizing"
    ANALYZING = "analyzing"
    AUDITING = "auditing"
    REWORKING = "reworking"
    SYNTHESIZING = "synthesizing"
    AWAITING_PUBLISH_APPROVAL = "awaiting_publish_approval"
    PUBLISHED = "published"
    CANCELLING = "cancelling"
    CANCELLED = "cancelled"
    FAILED = "failed"


class EvidenceStatus(StrEnum):
    ACTIVE = "active"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"
    INACCESSIBLE = "inaccessible"


class ClaimStatus(StrEnum):
    SUPPORTED = "supported"
    CONTRADICTED = "contradicted"
    UNCERTAIN = "uncertain"


class ClaimEvidenceRelation(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CONTEXT = "context"


class ResearchScope(BaseModel):
    market: str = Field(default="中国+全球", min_length=1, max_length=128)
    audience: str = Field(default="产品与战略团队", min_length=1, max_length=256)
    language: Literal["zh-CN", "en-US"] = "zh-CN"
    time_range: str = Field(default="最近12个月", min_length=1, max_length=128)
    competitors: list[str] = Field(min_length=2, max_length=5)
    dimensions: list[str] = Field(default_factory=lambda: ["功能", "定价", "定位", "用户", "壁垒"], min_length=1, max_length=12)

    @field_validator("competitors", "dimensions")
    @classmethod
    def normalize_unique_values(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            item = value.strip()
            if item and item.casefold() not in {entry.casefold() for entry in normalized}:
                normalized.append(item)
        return normalized

    @model_validator(mode="after")
    def require_unique_cardinality(self) -> ResearchScope:
        if not 2 <= len(self.competitors) <= 5:
            raise ValueError("Scope requires 2-5 unique competitors")
        if not self.dimensions:
            raise ValueError("Scope requires at least one research dimension")
        return self


class InvestigationCreate(BaseModel):
    investigation_type: InvestigationType = InvestigationType.COMPETITIVE_RESEARCH
    title: str = Field(min_length=1, max_length=200)
    brief: str = Field(min_length=10, max_length=10_000)
    project_id: str | None = Field(default=None, max_length=64)
    scope: ResearchScope


class InvestigationSummary(BaseModel):
    id: str
    investigation_type: InvestigationType
    title: str
    brief: str
    status: InvestigationStatus
    project_id: str | None = None
    organization_id: str | None = None
    workflow_version: str
    scope: ResearchScope
    rework_round: int
    token_used: int
    token_budget: int
    created_at: datetime
    updated_at: datetime


class ScopePatch(BaseModel):
    scope: ResearchScope
    expected_version: int = Field(ge=1)


class ApprovalRequest(BaseModel):
    idempotency_key: str = Field(min_length=8, max_length=128)


class ReworkRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=5000)
    idempotency_key: str = Field(min_length=8, max_length=128)


class EvidenceCreate(BaseModel):
    competitor_id: str | None = None
    source_url: HttpUrl
    canonical_url: HttpUrl
    source_domain: str = Field(min_length=1, max_length=255)
    source_type: str = Field(default="web", max_length=32)
    title: str = Field(min_length=1, max_length=500)
    publisher: str | None = Field(default=None, max_length=255)
    author: str | None = Field(default=None, max_length=255)
    published_at: datetime | None = None
    retrieved_at: datetime
    excerpt: str = Field(min_length=1, max_length=20_000)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_ref: str | None = Field(default=None, max_length=1024)
    language: str = Field(default="zh-CN", max_length=16)
    source_authority: int = Field(ge=0, le=30)
    freshness: int = Field(ge=0, le=20)
    extraction_quality: int = Field(ge=0, le=10)
    specificity: int = Field(ge=0, le=10)
    corroboration: int = Field(ge=0, le=30)
    status: EvidenceStatus = EvidenceStatus.ACTIVE


class EvidenceView(EvidenceCreate):
    id: str
    investigation_id: str
    credibility_score: int


class ClaimCreate(BaseModel):
    competitor_id: str | None = None
    dimension: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=5000)
    material: bool = True
    claim_type: str = Field(default="fact", max_length=32)
    evidence_ids: list[str] = Field(default_factory=list)


class ClaimView(ClaimCreate):
    id: str
    investigation_id: str
    status: ClaimStatus
    independent_source_count: int


class InvestigationEvent(BaseModel):
    seq: int
    investigation_id: str
    event_type: str
    stage: str | None = None
    run_id: str | None = None
    task_id: str | None = None
    trace_id: str | None = None
    payload: dict[str, Any]
    created_at: datetime
