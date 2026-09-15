from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from urllib.parse import urlsplit

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


class SourceType(StrEnum):
    OFFICIAL = "official"
    PRICING = "pricing"
    DOCUMENTATION = "documentation"
    GITHUB = "github"
    NEWS = "news"
    REVIEW = "review"
    FINANCIAL_REPORT = "financial_report"
    USER_UPLOAD = "user_upload"
    WEB = "web"


class ResearchScope(BaseModel):
    market: str = Field(default="中国+全球", min_length=1, max_length=128)
    audience: str = Field(default="产品与战略团队", min_length=1, max_length=256)
    language: Literal["zh-CN", "en-US"] = "zh-CN"
    time_range: str = Field(default="最近12个月", min_length=1, max_length=128)
    competitors: list[str] = Field(min_length=2, max_length=5)
    dimensions: list[str] = Field(default_factory=lambda: ["功能", "定价", "定位", "用户", "壁垒"], min_length=1, max_length=12)
    official_domains: dict[str, list[str]] = Field(default_factory=dict)

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
        competitor_names = {name.casefold() for name in self.competitors}
        if any(name.casefold() not in competitor_names for name in self.official_domains):
            raise ValueError("official_domains keys must match scoped competitors")
        normalized_domains: dict[str, list[str]] = {}
        for name, domains in self.official_domains.items():
            normalized: set[str] = set()
            for domain in domains:
                value = domain.strip().lower()
                parsed = urlsplit(value if "://" in value else f"//{value}")
                hostname = (parsed.hostname or "").removeprefix("www.")
                if "." in hostname:
                    normalized.add(hostname)
            normalized_domains[name] = sorted(normalized)
        self.official_domains = normalized_domains
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
    failure_retry_count: int
    token_used: int
    token_budget: int
    deadline_at: datetime | None = None
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
    source_type: SourceType = SourceType.WEB
    title: str = Field(min_length=1, max_length=500)
    publisher: str | None = Field(default=None, max_length=255)
    author: str | None = Field(default=None, max_length=255)
    published_at: datetime | None = None
    retrieved_at: datetime
    excerpt: str = Field(min_length=1, max_length=20_000)
    snapshot_text: str = Field(min_length=1, max_length=500_000, exclude=True)
    snapshot_mime_type: str = Field(default="text/plain", max_length=128)
    extraction_method: str = Field(default="unknown", max_length=64)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    snapshot_ref: str | None = Field(default=None, max_length=1024)
    original_ref: str | None = Field(default=None, max_length=1024, exclude=True)
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


class ClaimEvidenceBinding(BaseModel):
    evidence_id: str = Field(min_length=8, max_length=64)
    relation: ClaimEvidenceRelation = ClaimEvidenceRelation.SUPPORTS
    verbatim_quote: str = Field(min_length=1, max_length=8000)
    quote_start: int | None = Field(default=None, ge=0)
    quote_end: int | None = Field(default=None, ge=1)
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class PriceObservationCreate(BaseModel):
    evidence_id: str = Field(min_length=8, max_length=64)
    plan_name: str = Field(min_length=1, max_length=200)
    amount: str = Field(min_length=1, max_length=64)
    currency: Literal["CNY", "USD", "EUR", "GBP"]
    billing_period: Literal["month", "year", "one_time", "usage"]
    billing_unit: str = Field(default="account", min_length=1, max_length=128)
    seat_minimum: int | None = Field(default=None, ge=1)
    region: str | None = Field(default=None, max_length=128)
    tax_included: bool | None = None
    promotion: bool = False
    effective_at: datetime | None = None
    official: bool
    verbatim_quote: str = Field(min_length=1, max_length=8000)
    snapshot_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ClaimCreate(BaseModel):
    competitor_id: str | None = None
    dimension: str = Field(min_length=1, max_length=64)
    text: str = Field(min_length=1, max_length=5000)
    material: bool = True
    claim_type: str = Field(default="fact", max_length=32)
    evidence_ids: list[str] = Field(default_factory=list)
    evidence_bindings: list[ClaimEvidenceBinding] = Field(default_factory=list, max_length=20)
    price_observations: list[PriceObservationCreate] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def require_verbatim_evidence_bindings(self) -> ClaimCreate:
        binding_ids = [binding.evidence_id for binding in self.evidence_bindings]
        if len(binding_ids) != len(set(binding_ids)):
            raise ValueError("Claim evidence bindings must reference unique Evidence IDs")
        if self.claim_type == "fact" and not self.evidence_bindings:
            raise ValueError("Factual claims require at least one verbatim Evidence binding")
        self.evidence_ids = binding_ids
        price_evidence = {item.evidence_id for item in self.price_observations}
        if price_evidence - set(binding_ids):
            raise ValueError("Price observations must reference a bound Evidence record")
        return self


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
