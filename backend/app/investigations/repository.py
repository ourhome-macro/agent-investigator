from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.investigations.contracts import (
    ClaimCreate,
    ClaimEvidenceRelation,
    ClaimStatus,
    EvidenceCreate,
    EvidenceStatus,
    InvestigationCreate,
    InvestigationStatus,
    ResearchScope,
)
from app.investigations.persistence.models import (
    ClaimEvidenceRow,
    ClaimRow,
    CompetitorRow,
    EvidenceRow,
    InvestigationEventRow,
    InvestigationRow,
    ReportRow,
    ReportSectionRow,
    ScopeRow,
)
from app.investigations.scoring import credibility_score, independent_source_count
from app.investigations.state_machine import require_transition


class InvestigationConflict(ValueError):
    pass


class InvestigationRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    @staticmethod
    def _id() -> str:
        return uuid.uuid4().hex

    async def create(self, request: InvestigationCreate, *, user_id: str) -> dict[str, Any]:
        now = datetime.now(UTC)
        investigation_id = self._id()
        async with self._sf() as session, session.begin():
            row = InvestigationRow(
                id=investigation_id,
                user_id=user_id,
                project_id=request.project_id,
                investigation_type=request.investigation_type.value,
                title=request.title,
                brief=request.brief,
                status=InvestigationStatus.AWAITING_SCOPE_APPROVAL.value,
                workflow_version="competitive-research-v1",
                deadline_at=now + timedelta(minutes=30),
                created_at=now,
                updated_at=now,
            )
            scope = ScopeRow(
                id=self._id(),
                investigation_id=investigation_id,
                version=1,
                market=request.scope.market,
                audience=request.scope.audience,
                language=request.scope.language,
                time_range=request.scope.time_range,
                dimensions=request.scope.dimensions,
                created_at=now,
                updated_at=now,
            )
            session.add_all([row, scope])
            for name in request.scope.competitors:
                session.add(CompetitorRow(id=self._id(), investigation_id=investigation_id, canonical_name=name))
            session.add(
                InvestigationEventRow(
                    investigation_id=investigation_id,
                    event_type="ci.stage.completed",
                    stage="planning",
                    payload={"next": InvestigationStatus.AWAITING_SCOPE_APPROVAL.value, "scope_version": 1},
                    created_at=now,
                )
            )
        result = await self.get(investigation_id, user_id=user_id)
        assert result is not None
        return result

    async def get(self, investigation_id: str, *, user_id: str) -> dict[str, Any] | None:
        async with self._sf() as session:
            row = await session.get(InvestigationRow, investigation_id)
            if row is None or row.user_id != user_id:
                return None
            scope = (await session.execute(select(ScopeRow).where(ScopeRow.investigation_id == investigation_id))).scalar_one()
            competitors = list((await session.execute(select(CompetitorRow).where(CompetitorRow.investigation_id == investigation_id).order_by(CompetitorRow.created_at))).scalars())
            return self._serialize(row, scope, competitors)

    async def list(self, *, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        async with self._sf() as session:
            ids = list((await session.execute(select(InvestigationRow.id).where(InvestigationRow.user_id == user_id).order_by(InvestigationRow.updated_at.desc()).limit(limit))).scalars())
        return [item for investigation_id in ids if (item := await self.get(investigation_id, user_id=user_id)) is not None]

    async def list_competitors(self, investigation_id: str, *, user_id: str) -> list[dict[str, str]] | None:
        if await self.get(investigation_id, user_id=user_id) is None:
            return None
        async with self._sf() as session:
            rows = list((await session.execute(select(CompetitorRow).where(CompetitorRow.investigation_id == investigation_id).order_by(CompetitorRow.created_at))).scalars())
            return [{"id": row.id, "name": row.canonical_name} for row in rows]

    async def list_recoverable(self) -> list[tuple[str, str]]:
        statuses = [
            InvestigationStatus.COLLECTING.value,
            InvestigationStatus.NORMALIZING.value,
            InvestigationStatus.ANALYZING.value,
            InvestigationStatus.AUDITING.value,
            InvestigationStatus.SYNTHESIZING.value,
        ]
        async with self._sf() as session:
            rows = await session.execute(select(InvestigationRow.id, InvestigationRow.user_id).where(InvestigationRow.status.in_(statuses)))
            return [(row.id, row.user_id) for row in rows]

    async def update_scope(self, investigation_id: str, scope: ResearchScope, *, expected_version: int, user_id: str) -> dict[str, Any] | None:
        async with self._sf() as session, session.begin():
            investigation = await session.get(InvestigationRow, investigation_id, with_for_update=True)
            if investigation is None or investigation.user_id != user_id:
                return None
            if investigation.status != InvestigationStatus.AWAITING_SCOPE_APPROVAL.value:
                raise InvestigationConflict("Scope can only change while awaiting approval")
            row = (await session.execute(select(ScopeRow).where(ScopeRow.investigation_id == investigation_id).with_for_update())).scalar_one()
            if row.version != expected_version:
                raise InvestigationConflict("Scope version changed; reload before updating")
            row.version += 1
            row.market, row.audience, row.language, row.time_range = scope.market, scope.audience, scope.language, scope.time_range
            row.dimensions, row.updated_at = scope.dimensions, datetime.now(UTC)
            await session.execute(CompetitorRow.__table__.delete().where(CompetitorRow.investigation_id == investigation_id))
            session.add_all([CompetitorRow(id=self._id(), investigation_id=investigation_id, canonical_name=name) for name in scope.competitors])
            investigation.updated_at = datetime.now(UTC)
        return await self.get(investigation_id, user_id=user_id)

    async def transition(self, investigation_id: str, target: InvestigationStatus, *, user_id: str, event_type: str, payload: dict[str, Any] | None = None) -> dict[str, Any] | None:
        async with self._sf() as session, session.begin():
            row = await session.get(InvestigationRow, investigation_id, with_for_update=True)
            if row is None or row.user_id != user_id:
                return None
            current = InvestigationStatus(row.status)
            require_transition(current, target)
            row.status, row.updated_at = target.value, datetime.now(UTC)
            session.add(InvestigationEventRow(investigation_id=investigation_id, event_type=event_type, stage=target.value, payload=payload or {}, created_at=datetime.now(UTC)))
        return await self.get(investigation_id, user_id=user_id)

    async def approve_scope(self, investigation_id: str, *, user_id: str, idempotency_key: str) -> dict[str, Any] | None:
        async with self._sf() as session, session.begin():
            row = await session.get(InvestigationRow, investigation_id, with_for_update=True)
            if row is None or row.user_id != user_id:
                return None
            if row.status != InvestigationStatus.COLLECTING.value:
                require_transition(InvestigationStatus(row.status), InvestigationStatus.COLLECTING)
                scope = (await session.execute(select(ScopeRow).where(ScopeRow.investigation_id == investigation_id).with_for_update())).scalar_one()
                scope.approved_at = datetime.now(UTC)
                row.status, row.updated_at = InvestigationStatus.COLLECTING.value, datetime.now(UTC)
                session.add(
                    InvestigationEventRow(investigation_id=investigation_id, event_type="ci.stage.started", stage="collecting", payload={"scope_version": scope.version, "idempotency_key": idempotency_key}, created_at=datetime.now(UTC))
                )
        return await self.get(investigation_id, user_id=user_id)

    async def add_evidence(self, investigation_id: str, request: EvidenceCreate, *, user_id: str, agent_name: str | None = None) -> dict[str, Any] | None:
        if await self.get(investigation_id, user_id=user_id) is None:
            return None
        components = {
            "source_authority": request.source_authority,
            "freshness": request.freshness,
            "extraction_quality": request.extraction_quality,
            "specificity": request.specificity,
            "corroboration": request.corroboration,
        }
        score = credibility_score(**components)
        row = EvidenceRow(
            id=self._id(),
            investigation_id=investigation_id,
            competitor_id=request.competitor_id,
            source_url=str(request.source_url),
            canonical_url=str(request.canonical_url),
            source_domain=request.source_domain,
            source_type=request.source_type,
            title=request.title,
            publisher=request.publisher,
            author=request.author,
            published_at=request.published_at,
            retrieved_at=request.retrieved_at,
            excerpt=request.excerpt,
            content_hash=request.content_hash,
            snapshot_ref=request.snapshot_ref,
            language=request.language,
            credibility_score=score,
            score_components=components,
            status=request.status.value,
            created_by_agent=agent_name,
        )
        try:
            async with self._sf() as session, session.begin():
                session.add(row)
                session.add(
                    InvestigationEventRow(
                        investigation_id=investigation_id, event_type="ci.evidence.accepted" if request.status == EvidenceStatus.ACTIVE else "ci.evidence.rejected", stage="collecting", payload={"evidence_id": row.id, "score": score}
                    )
                )
        except IntegrityError as exc:
            raise InvestigationConflict("Evidence content already exists in this investigation") from exc
        return self._evidence_dict(row)

    async def list_evidence(self, investigation_id: str, *, user_id: str) -> list[dict[str, Any]] | None:
        if await self.get(investigation_id, user_id=user_id) is None:
            return None
        async with self._sf() as session:
            rows = list((await session.execute(select(EvidenceRow).where(EvidenceRow.investigation_id == investigation_id).order_by(EvidenceRow.created_at))).scalars())
            return [self._evidence_dict(row) for row in rows]

    async def add_claim(self, investigation_id: str, request: ClaimCreate, *, user_id: str, agent_name: str | None = None) -> dict[str, Any] | None:
        if await self.get(investigation_id, user_id=user_id) is None:
            return None
        async with self._sf() as session, session.begin():
            evidence_rows = (
                list((await session.execute(select(EvidenceRow).where(EvidenceRow.investigation_id == investigation_id, EvidenceRow.id.in_(request.evidence_ids), EvidenceRow.status == EvidenceStatus.ACTIVE.value))).scalars())
                if request.evidence_ids
                else []
            )
            if len(evidence_rows) != len(set(request.evidence_ids)):
                raise InvestigationConflict("Claim references missing or inactive evidence")
            count = independent_source_count(row.source_domain for row in evidence_rows)
            status = ClaimStatus.SUPPORTED if count >= (2 if request.material else 1) else ClaimStatus.UNCERTAIN
            claim = ClaimRow(
                id=self._id(),
                investigation_id=investigation_id,
                competitor_id=request.competitor_id,
                dimension=request.dimension,
                text=request.text,
                normalized_text=" ".join(request.text.casefold().split()),
                material=request.material,
                claim_type=request.claim_type,
                status=status.value,
                independent_source_count=count,
                created_by_agent=agent_name,
            )
            session.add(claim)
            await session.flush()
            session.add_all([ClaimEvidenceRow(claim_id=claim.id, evidence_id=row.id, relation=ClaimEvidenceRelation.SUPPORTS.value, support_score=row.credibility_score) for row in evidence_rows])
            session.add(InvestigationEventRow(investigation_id=investigation_id, event_type="ci.claim.created", stage="analyzing", payload={"claim_id": claim.id, "status": status.value, "independent_sources": count}))
        return self._claim_dict(claim, request.evidence_ids)

    async def list_claims(self, investigation_id: str, *, user_id: str) -> list[dict[str, Any]] | None:
        if await self.get(investigation_id, user_id=user_id) is None:
            return None
        async with self._sf() as session:
            rows = list((await session.execute(select(ClaimRow).where(ClaimRow.investigation_id == investigation_id).order_by(ClaimRow.created_at))).scalars())
            links = list((await session.execute(select(ClaimEvidenceRow).join(ClaimRow, ClaimRow.id == ClaimEvidenceRow.claim_id).where(ClaimRow.investigation_id == investigation_id))).scalars())
            by_claim: dict[str, list[str]] = {}
            for link in links:
                by_claim.setdefault(link.claim_id, []).append(link.evidence_id)
            return [self._claim_dict(row, by_claim.get(row.id, [])) for row in rows]

    async def list_events(self, investigation_id: str, *, user_id: str, after_seq: int = 0, limit: int = 500) -> list[dict[str, Any]] | None:
        if await self.get(investigation_id, user_id=user_id) is None:
            return None
        async with self._sf() as session:
            rows = list(
                (await session.execute(select(InvestigationEventRow).where(InvestigationEventRow.investigation_id == investigation_id, InvestigationEventRow.seq > after_seq).order_by(InvestigationEventRow.seq).limit(limit))).scalars()
            )
            return [
                {
                    "seq": row.seq,
                    "investigation_id": row.investigation_id,
                    "event_type": row.event_type,
                    "stage": row.stage,
                    "run_id": row.run_id,
                    "task_id": row.task_id,
                    "trace_id": row.trace_id,
                    "payload": row.payload,
                    "created_at": row.created_at,
                }
                for row in rows
            ]

    async def latest_report(self, investigation_id: str, *, user_id: str) -> dict[str, Any] | None:
        if await self.get(investigation_id, user_id=user_id) is None:
            return None
        async with self._sf() as session:
            row = (await session.execute(select(ReportRow).where(ReportRow.investigation_id == investigation_id).order_by(ReportRow.version.desc()).limit(1))).scalar_one_or_none()
            return (
                None
                if row is None
                else {
                    "id": row.id,
                    "investigation_id": row.investigation_id,
                    "version": row.version,
                    "status": row.status,
                    "schema_version": row.schema_version,
                    "structured_data": row.structured_data,
                    "rendered_markdown": row.rendered_markdown,
                    "approved_at": row.approved_at,
                    "created_at": row.created_at,
                }
            )

    async def create_report(self, investigation_id: str, *, structured_data: dict[str, Any], rendered_markdown: str, user_id: str) -> dict[str, Any] | None:
        if await self.get(investigation_id, user_id=user_id) is None:
            return None
        async with self._sf() as session, session.begin():
            latest = (await session.execute(select(func.max(ReportRow.version)).where(ReportRow.investigation_id == investigation_id))).scalar_one_or_none() or 0
            report = ReportRow(id=self._id(), investigation_id=investigation_id, version=latest + 1, status="review", structured_data=structured_data, rendered_markdown=rendered_markdown)
            session.add(report)
            await session.flush()
            for position, section in enumerate(structured_data.get("sections", [])):
                session.add(
                    ReportSectionRow(
                        id=str(section.get("id") or self._id()),
                        report_id=report.id,
                        section_type=str(section.get("type") or "unknown"),
                        position=position,
                        structured_data=section,
                        rendered_markdown=str(section.get("markdown") or ""),
                        claim_ids=list(section.get("claim_ids") or []),
                        evidence_ids=list(section.get("evidence_ids") or []),
                    )
                )
            investigation = await session.get(InvestigationRow, investigation_id, with_for_update=True)
            assert investigation is not None
            current = InvestigationStatus(investigation.status)
            if current != InvestigationStatus.AWAITING_PUBLISH_APPROVAL:
                require_transition(current, InvestigationStatus.AWAITING_PUBLISH_APPROVAL)
                investigation.status = InvestigationStatus.AWAITING_PUBLISH_APPROVAL.value
            session.add(InvestigationEventRow(investigation_id=investigation_id, event_type="ci.report.ready_for_review", stage="awaiting_publish_approval", payload={"report_id": report.id, "version": report.version}))
        return await self.latest_report(investigation_id, user_id=user_id)

    async def approve_report(self, investigation_id: str, report_version: int, *, user_id: str, idempotency_key: str) -> dict[str, Any] | None:
        async with self._sf() as session, session.begin():
            investigation = await session.get(InvestigationRow, investigation_id, with_for_update=True)
            if investigation is None or investigation.user_id != user_id:
                return None
            report = (await session.execute(select(ReportRow).where(ReportRow.investigation_id == investigation_id, ReportRow.version == report_version).with_for_update())).scalar_one_or_none()
            if report is None:
                raise InvestigationConflict("Report version not found")
            if report.status != "published":
                require_transition(InvestigationStatus(investigation.status), InvestigationStatus.PUBLISHED)
                now = datetime.now(UTC)
                report.status, report.approved_at = "published", now
                investigation.status, investigation.updated_at = InvestigationStatus.PUBLISHED.value, now
                session.add(InvestigationEventRow(investigation_id=investigation_id, event_type="ci.report.published", stage="published", payload={"report_id": report.id, "version": report.version, "idempotency_key": idempotency_key}))
        return await self.latest_report(investigation_id, user_id=user_id)

    async def reject_report(
        self,
        investigation_id: str,
        report_version: int,
        *,
        user_id: str,
        reason: str,
        idempotency_key: str,
        section_id: str | None = None,
    ) -> dict[str, Any] | None:
        async with self._sf() as session, session.begin():
            investigation = await session.get(InvestigationRow, investigation_id, with_for_update=True)
            if investigation is None or investigation.user_id != user_id:
                return None
            if investigation.rework_round >= 2:
                raise InvestigationConflict("Maximum report rework rounds reached")
            report = (await session.execute(select(ReportRow).where(ReportRow.investigation_id == investigation_id, ReportRow.version == report_version).with_for_update())).scalar_one_or_none()
            if report is None:
                raise InvestigationConflict("Report version not found")
            require_transition(InvestigationStatus(investigation.status), InvestigationStatus.REWORKING)
            investigation.status = InvestigationStatus.REWORKING.value
            investigation.rework_round += 1
            investigation.updated_at = datetime.now(UTC)
            report.status = "rejected"
            session.add(
                InvestigationEventRow(
                    investigation_id=investigation_id,
                    event_type="ci.rework.requested",
                    stage="reworking",
                    payload={
                        "report_id": report.id,
                        "version": report.version,
                        "reason": reason,
                        "section_id": section_id,
                        "round": investigation.rework_round,
                        "idempotency_key": idempotency_key,
                    },
                )
            )
        return await self.get(investigation_id, user_id=user_id)

    @staticmethod
    def _serialize(row: InvestigationRow, scope: ScopeRow, competitors: list[CompetitorRow]) -> dict[str, Any]:
        return {
            "id": row.id,
            "investigation_type": row.investigation_type,
            "title": row.title,
            "brief": row.brief,
            "status": row.status,
            "project_id": row.project_id,
            "organization_id": row.organization_id,
            "workflow_version": row.workflow_version,
            "scope": {
                "market": scope.market,
                "audience": scope.audience,
                "language": scope.language,
                "time_range": scope.time_range,
                "competitors": [item.canonical_name for item in competitors],
                "dimensions": scope.dimensions,
                "version": scope.version,
                "approved_at": scope.approved_at,
            },
            "rework_round": row.rework_round,
            "token_used": row.token_used,
            "token_budget": row.token_budget,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    @staticmethod
    def _evidence_dict(row: EvidenceRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "investigation_id": row.investigation_id,
            "competitor_id": row.competitor_id,
            "source_url": row.source_url,
            "canonical_url": row.canonical_url,
            "source_domain": row.source_domain,
            "source_type": row.source_type,
            "title": row.title,
            "publisher": row.publisher,
            "author": row.author,
            "published_at": row.published_at,
            "retrieved_at": row.retrieved_at,
            "excerpt": row.excerpt,
            "content_hash": row.content_hash,
            "snapshot_ref": row.snapshot_ref,
            "language": row.language,
            "credibility_score": row.credibility_score,
            "score_components": row.score_components,
            "status": row.status,
        }

    @staticmethod
    def _claim_dict(row: ClaimRow, evidence_ids: list[str]) -> dict[str, Any]:
        return {
            "id": row.id,
            "investigation_id": row.investigation_id,
            "competitor_id": row.competitor_id,
            "dimension": row.dimension,
            "text": row.text,
            "material": row.material,
            "claim_type": row.claim_type,
            "evidence_ids": evidence_ids,
            "status": row.status,
            "independent_source_count": row.independent_source_count,
        }
