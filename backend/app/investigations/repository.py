from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.investigations.confidence import POLICY_VERSION, assess_support, claim_display_text, completion_state, is_official_source, issue_is_blocking
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
from app.investigations.embeddings import EmbeddingProvider
from app.investigations.evidence_validation import (
    EvidenceValidationError,
    locate_verbatim_quote,
    require_claim_values_in_quotes,
    require_price_fields_in_quote,
    sha256_text,
)
from app.investigations.persistence.models import (
    AuditIssueRow,
    BudgetEntryRow,
    BudgetReservationRow,
    ClaimEvidenceRow,
    ClaimRow,
    ClaimSubmissionRow,
    CompetitorRow,
    EvidenceChunkRow,
    EvidenceRow,
    EvidenceSnapshotRow,
    ExportRow,
    InvestigationEventRow,
    InvestigationRow,
    PriceObservationRow,
    ReportRow,
    ReportSectionRow,
    ResearchCandidateRow,
    ScopeRow,
    StageAttemptRow,
    WorkflowRunRow,
)
from app.investigations.retrieval import chunk_snapshot, hashed_embedding, rank_chunks
from app.investigations.scoring import credibility_score, independent_source_count
from app.investigations.state_machine import require_transition
from app.investigations.storage import ArtifactStorage


class InvestigationConflict(ValueError):
    pass


class InvestigationBudgetExceeded(RuntimeError):
    pass


class InvestigationDeadlineExceeded(RuntimeError):
    pass


def _utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class InvestigationRepository:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        artifact_storage: ArtifactStorage | None = None,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self._sf = session_factory
        self._artifact_storage = artifact_storage
        self._embedding_provider = embedding_provider

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
                status=InvestigationStatus.PLANNING.value,
                workflow_version="competitive-research-v2",
                token_budget=min(525_000, 300_000 + max(0, len(request.scope.competitors) - 2) * 75_000),
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
                required_dimensions=request.scope.required_dimensions,
                created_at=now,
                updated_at=now,
            )
            # Flush the parent explicitly. Gateway SQLite enables foreign-key
            # enforcement, and these models intentionally have no ORM
            # relationships from which SQLAlchemy could infer flush ordering.
            session.add(row)
            await session.flush()
            session.add(scope)
            for name in request.scope.competitors:
                session.add(
                    CompetitorRow(
                        id=self._id(),
                        investigation_id=investigation_id,
                        canonical_name=name,
                        official_domains=request.scope.official_domains.get(name, []),
                        official_repositories=request.scope.official_repositories.get(name, []),
                    )
                )
            session.add(
                InvestigationEventRow(
                    investigation_id=investigation_id,
                    event_type="ci.stage.started",
                    stage="planning",
                    payload={"scope_version": 1},
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
            return [{"id": row.id, "name": row.canonical_name, "official_domains": row.official_domains, "official_repositories": row.official_repositories} for row in rows]

    async def list_recoverable(self) -> list[tuple[str, str]]:
        statuses = [
            InvestigationStatus.PLANNING.value,
            InvestigationStatus.COLLECTING.value,
            InvestigationStatus.NORMALIZING.value,
            InvestigationStatus.ANALYZING.value,
            InvestigationStatus.AUDITING.value,
            InvestigationStatus.REWORKING.value,
            InvestigationStatus.SYNTHESIZING.value,
        ]
        async with self._sf() as session:
            rows = await session.execute(select(InvestigationRow.id, InvestigationRow.user_id).where(InvestigationRow.status.in_(statuses)))
            return [(row.id, row.user_id) for row in rows]

    async def assert_execution_budget(
        self,
        investigation_id: str,
        *,
        user_id: str,
        stage: str,
        estimated_tokens: int,
    ) -> dict[str, int]:
        async with self._sf() as session:
            investigation = await session.get(InvestigationRow, investigation_id)
            if investigation is None or investigation.user_id != user_id:
                raise LookupError("Investigation not found")
            now = datetime.now(UTC)
            if investigation.deadline_at is not None and _utc(investigation.deadline_at) <= now:
                raise InvestigationDeadlineExceeded("Investigation deadline has expired")
            reserve_stages = {"auditing", "reworking", "synthesizing"}
            usable_budget = investigation.token_budget if stage in reserve_stages else int(investigation.token_budget * 0.8)
            if investigation.token_used + investigation.token_reserved + max(0, estimated_tokens) > usable_budget:
                raise InvestigationBudgetExceeded(f"Stage {stage} would exceed its token budget: used={investigation.token_used}, estimated={estimated_tokens}, usable={usable_budget}")
            return {
                "used": investigation.token_used,
                "budget": investigation.token_budget,
                "usable": usable_budget,
                "remaining": max(0, usable_budget - investigation.token_used - investigation.token_reserved),
            }

    async def reserve_budget(self, investigation_id: str, *, user_id: str, stage: str, reservation_key: str, tokens: int) -> None:
        try:
            await self._reserve_budget_once(investigation_id, user_id=user_id, stage=stage, reservation_key=reservation_key, tokens=tokens)
        except IntegrityError:
            await self._reserve_budget_once(investigation_id, user_id=user_id, stage=stage, reservation_key=reservation_key, tokens=tokens)

    async def _reserve_budget_once(self, investigation_id: str, *, user_id: str, stage: str, reservation_key: str, tokens: int) -> None:
        if tokens <= 0:
            raise ValueError("Reservation must be positive")
        key = f"{investigation_id}:{reservation_key}"
        async with self._sf() as session, session.begin():
            row = await session.get(InvestigationRow, investigation_id)
            if row is None or row.user_id != user_id:
                raise LookupError("Investigation not found")
            existing = await session.get(BudgetReservationRow, key)
            if existing is not None:
                if existing.tokens != tokens or existing.stage != stage:
                    raise InvestigationConflict("Budget reservation input changed")
                return
            usable = row.token_budget if stage in {"auditing", "synthesizing"} else int(row.token_budget * 0.8)
            result = await session.execute(
                update(InvestigationRow)
                .where(
                    InvestigationRow.id == investigation_id,
                    InvestigationRow.user_id == user_id,
                    InvestigationRow.token_used + InvestigationRow.token_reserved + tokens <= usable,
                    (InvestigationRow.deadline_at.is_(None)) | (InvestigationRow.deadline_at > datetime.now(UTC)),
                )
                .values(token_reserved=InvestigationRow.token_reserved + tokens)
            )
            if result.rowcount != 1:
                raise InvestigationBudgetExceeded("Insufficient unreserved investigation budget or deadline expired")
            session.add(BudgetReservationRow(id=key, investigation_id=investigation_id, stage=stage, tokens=tokens))

    async def settle_budget(self, investigation_id: str, *, user_id: str, reservation_key: str, actual_tokens: int, already_recorded: bool = False) -> None:
        if actual_tokens < 0:
            raise ValueError("Token usage cannot be negative")
        async with self._sf() as session, session.begin():
            row = await session.get(InvestigationRow, investigation_id, with_for_update=True)
            if row is None or row.user_id != user_id:
                raise LookupError("Investigation not found")
            reservation = await session.get(BudgetReservationRow, f"{investigation_id}:{reservation_key}")
            if reservation is None:
                raise InvestigationConflict("Missing budget reservation")
            result = await session.execute(update(BudgetReservationRow).where(BudgetReservationRow.id == reservation.id, BudgetReservationRow.actual_tokens.is_(None)).values(actual_tokens=actual_tokens))
            if result.rowcount != 1:
                return
            await session.execute(
                update(InvestigationRow)
                .where(InvestigationRow.id == investigation_id)
                .values(token_reserved=InvestigationRow.token_reserved - reservation.tokens, token_used=InvestigationRow.token_used + (0 if already_recorded else actual_tokens))
            )
            session.add(
                InvestigationEventRow(
                    investigation_id=investigation_id,
                    stage=reservation.stage,
                    event_type="ci.budget.settled",
                    payload={"reservation_key": reservation_key, "reserved": reservation.tokens, "actual": actual_tokens, "overrun": actual_tokens > reservation.tokens},
                )
            )

    async def record_control_event(self, investigation_id: str, *, user_id: str, event_type: str, payload: dict, stage: str | None = None) -> None:
        async with self._sf() as session, session.begin():
            row = await session.get(InvestigationRow, investigation_id)
            if row is None or row.user_id != user_id:
                raise LookupError("Investigation not found")
            session.add(InvestigationEventRow(investigation_id=investigation_id, event_type=event_type, payload=payload, stage=stage))

    async def save_candidate(self, investigation_id: str, *, user_id: str, payload: dict) -> str:
        key = sha256_text(f"{investigation_id}:{payload['competitor_id']}:{payload['dimension']}:{payload['url']}:{payload['content_hash']}")
        async with self._sf() as session, session.begin():
            investigation = await session.get(InvestigationRow, investigation_id, with_for_update=True)
            if investigation is None or investigation.user_id != user_id:
                raise LookupError("Investigation not found")
            if await session.get(ResearchCandidateRow, key) is None:
                session.add(ResearchCandidateRow(id=key, investigation_id=investigation_id, payload=payload))
        return key

    async def get_candidate(self, investigation_id: str, candidate_id: str, *, user_id: str) -> dict | None:
        if await self.get(investigation_id, user_id=user_id) is None:
            return None
        async with self._sf() as session:
            row = await session.get(ResearchCandidateRow, candidate_id)
            return row.payload if row is not None and row.investigation_id == investigation_id else None

    async def record_token_usage(
        self,
        investigation_id: str,
        *,
        user_id: str,
        workflow_run_id: str | None,
        stage: str,
        task_id: str | None,
        run_id: str | None,
        durable_batch_id: str | None,
        model_name: str | None,
        token_usage: dict[str, Any] | None,
        idempotency_key: str,
    ) -> dict[str, int]:
        usage = token_usage or {}
        input_tokens = max(0, int(usage.get("input_tokens") or 0))
        output_tokens = max(0, int(usage.get("output_tokens") or 0))
        total_tokens = max(0, int(usage.get("total_tokens") or input_tokens + output_tokens))
        async with self._sf() as session, session.begin():
            investigation = await session.get(InvestigationRow, investigation_id, with_for_update=True)
            if investigation is None or investigation.user_id != user_id:
                raise LookupError("Investigation not found")
            existing = (await session.execute(select(BudgetEntryRow).where(BudgetEntryRow.idempotency_key == idempotency_key))).scalar_one_or_none()
            if existing is not None:
                return {"used": investigation.token_used, "budget": investigation.token_budget}
            previous = investigation.token_used
            await session.execute(update(InvestigationRow).where(InvestigationRow.id == investigation_id).values(token_used=InvestigationRow.token_used + total_tokens))
            session.add(
                BudgetEntryRow(
                    id=self._id(),
                    investigation_id=investigation_id,
                    workflow_run_id=workflow_run_id,
                    stage=stage,
                    task_id=task_id,
                    run_id=run_id,
                    durable_batch_id=durable_batch_id,
                    model_name=model_name,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    total_tokens=total_tokens,
                    idempotency_key=idempotency_key,
                )
            )
            if previous < int(investigation.token_budget * 0.8) <= investigation.token_used:
                session.add(
                    InvestigationEventRow(
                        investigation_id=investigation_id,
                        event_type="ci.budget.warning",
                        stage=stage,
                        task_id=task_id,
                        payload={"used": investigation.token_used, "budget": investigation.token_budget},
                    )
                )
            if previous < investigation.token_budget <= investigation.token_used:
                session.add(
                    InvestigationEventRow(
                        investigation_id=investigation_id,
                        event_type="ci.budget.exhausted",
                        stage=stage,
                        task_id=task_id,
                        payload={"used": investigation.token_used, "budget": investigation.token_budget},
                    )
                )
        return {"used": investigation.token_used, "budget": investigation.token_budget}

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
            row.required_dimensions = scope.required_dimensions
            await session.execute(CompetitorRow.__table__.delete().where(CompetitorRow.investigation_id == investigation_id))
            session.add_all(
                [
                    CompetitorRow(
                        id=self._id(),
                        investigation_id=investigation_id,
                        canonical_name=name,
                        official_domains=scope.official_domains.get(name, []),
                        official_repositories=scope.official_repositories.get(name, []),
                    )
                    for name in scope.competitors
                ]
            )
            investigation.updated_at = datetime.now(UTC)
        return await self.get(investigation_id, user_id=user_id)

    async def complete_planning(self, investigation_id: str, scope: ResearchScope, *, user_id: str, run_id: str | None) -> dict[str, Any] | None:
        async with self._sf() as session, session.begin():
            investigation = await session.get(InvestigationRow, investigation_id, with_for_update=True)
            if investigation is None or investigation.user_id != user_id:
                return None
            if investigation.status != InvestigationStatus.PLANNING.value:
                raise InvestigationConflict("Planning can only complete from planning state")
            scope_row = (await session.execute(select(ScopeRow).where(ScopeRow.investigation_id == investigation_id).with_for_update())).scalar_one()
            scope_row.version += 1
            scope_row.market = scope.market
            scope_row.audience = scope.audience
            scope_row.language = scope.language
            scope_row.time_range = scope.time_range
            scope_row.dimensions = scope.dimensions
            scope_row.required_dimensions = scope.required_dimensions
            scope_row.updated_at = datetime.now(UTC)
            await session.execute(CompetitorRow.__table__.delete().where(CompetitorRow.investigation_id == investigation_id))
            session.add_all(
                [
                    CompetitorRow(
                        id=self._id(),
                        investigation_id=investigation_id,
                        canonical_name=name,
                        official_domains=scope.official_domains.get(name, []),
                        official_repositories=scope.official_repositories.get(name, []),
                    )
                    for name in scope.competitors
                ]
            )
            require_transition(InvestigationStatus.PLANNING, InvestigationStatus.AWAITING_SCOPE_APPROVAL)
            investigation.status = InvestigationStatus.AWAITING_SCOPE_APPROVAL.value
            investigation.updated_at = datetime.now(UTC)
            session.add(
                InvestigationEventRow(
                    investigation_id=investigation_id,
                    event_type="ci.stage.completed",
                    stage="planning",
                    run_id=run_id,
                    payload={"next": InvestigationStatus.AWAITING_SCOPE_APPROVAL.value, "scope_version": scope_row.version},
                )
            )
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
        if sha256_text(request.snapshot_text) != request.content_hash:
            raise InvestigationConflict("Evidence content_hash does not match snapshot_text")
        try:
            quote_match = locate_verbatim_quote(request.snapshot_text, request.excerpt)
        except EvidenceValidationError as exc:
            raise InvestigationConflict(str(exc)) from exc
        allowed_stages = {
            InvestigationStatus.PLANNING.value,
            InvestigationStatus.AWAITING_SCOPE_APPROVAL.value,
            InvestigationStatus.COLLECTING.value,
            InvestigationStatus.REWORKING.value,
        }
        async with self._sf() as session:
            investigation = await session.get(InvestigationRow, investigation_id)
            if investigation is None or investigation.user_id != user_id:
                return None
            if investigation.status not in allowed_stages:
                raise InvestigationConflict("Evidence can only be submitted before publication analysis or during rework")
        components = {
            "source_authority": request.source_authority,
            "freshness": request.freshness,
            "extraction_quality": request.extraction_quality,
            "specificity": request.specificity,
            "corroboration": request.corroboration,
        }
        score = credibility_score(**components)
        chunks = chunk_snapshot(request.snapshot_text)
        embeddings = await self._embedding_provider.embed_texts([chunk.content for chunk in chunks]) if self._embedding_provider is not None else [hashed_embedding(chunk.content) for chunk in chunks]
        object_ref = request.snapshot_ref
        if self._artifact_storage is not None:
            object_ref = await self._artifact_storage.put_bytes(
                f"{investigation_id}/snapshots/{request.content_hash}.txt",
                request.snapshot_text.encode("utf-8"),
                content_type=request.snapshot_mime_type,
            )
        try:
            async with self._sf() as session, session.begin():
                investigation = await session.get(InvestigationRow, investigation_id, with_for_update=True)
                if investigation is None or investigation.user_id != user_id:
                    return None
                if investigation.status not in allowed_stages:
                    raise InvestigationConflict("Evidence can only be submitted before publication analysis or during rework")
                existing = (
                    await session.execute(
                        select(EvidenceRow).where(
                            EvidenceRow.investigation_id == investigation_id,
                            EvidenceRow.content_hash == request.content_hash,
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    return self._evidence_dict(existing)
                snapshot = (
                    await session.execute(
                        select(EvidenceSnapshotRow).where(
                            EvidenceSnapshotRow.investigation_id == investigation_id,
                            EvidenceSnapshotRow.content_hash == request.content_hash,
                        )
                    )
                ).scalar_one_or_none()
                if snapshot is None:
                    snapshot = EvidenceSnapshotRow(
                        id=self._id(),
                        investigation_id=investigation_id,
                        source_url=str(request.canonical_url),
                        content_text=request.snapshot_text,
                        content_hash=request.content_hash,
                        mime_type=request.snapshot_mime_type,
                        extraction_method=request.extraction_method,
                        language=request.language,
                        object_ref=object_ref,
                        original_ref=request.original_ref,
                    )
                    session.add(snapshot)
                    await session.flush()
                    session.add_all(
                        [
                            EvidenceChunkRow(
                                id=self._id(),
                                snapshot_id=snapshot.id,
                                ordinal=chunk.ordinal,
                                char_start=chunk.char_start,
                                char_end=chunk.char_end,
                                content=chunk.content,
                                content_hash=chunk.content_hash,
                                token_estimate=chunk.token_estimate,
                                embedding=embedding,
                            )
                            for chunk, embedding in zip(chunks, embeddings, strict=True)
                        ]
                    )
                row = EvidenceRow(
                    id=self._id(),
                    snapshot_id=snapshot.id,
                    investigation_id=investigation_id,
                    competitor_id=request.competitor_id,
                    source_url=str(request.source_url),
                    canonical_url=str(request.canonical_url),
                    source_domain=request.source_domain,
                    source_type=request.source_type.value,
                    title=request.title,
                    publisher=request.publisher,
                    author=request.author,
                    published_at=request.published_at,
                    retrieved_at=request.retrieved_at,
                    excerpt=quote_match.quote,
                    content_hash=request.content_hash,
                    snapshot_ref=object_ref or snapshot.id,
                    language=request.language,
                    credibility_score=score,
                    score_components=components,
                    status=request.status.value,
                    created_by_agent=agent_name,
                )
                session.add(row)
                session.add(
                    InvestigationEventRow(
                        investigation_id=investigation_id,
                        event_type="ci.evidence.accepted" if request.status == EvidenceStatus.ACTIVE else "ci.evidence.rejected",
                        stage=investigation.status,
                        payload={"evidence_id": row.id, "score": score},
                    )
                )
        except IntegrityError:
            async with self._sf() as session:
                existing = (
                    await session.execute(
                        select(EvidenceRow).where(
                            EvidenceRow.investigation_id == investigation_id,
                            EvidenceRow.content_hash == request.content_hash,
                        )
                    )
                ).scalar_one_or_none()
                if existing is not None:
                    return self._evidence_dict(existing)
            raise InvestigationConflict("Evidence content already exists in this investigation") from None
        return self._evidence_dict(row)

    async def list_evidence(self, investigation_id: str, *, user_id: str) -> list[dict[str, Any]] | None:
        if await self.get(investigation_id, user_id=user_id) is None:
            return None
        async with self._sf() as session:
            rows = list((await session.execute(select(EvidenceRow).where(EvidenceRow.investigation_id == investigation_id).order_by(EvidenceRow.created_at))).scalars())
            return [self._evidence_dict(row) for row in rows]

    async def add_claim(
        self,
        investigation_id: str,
        request: ClaimCreate,
        *,
        user_id: str,
        agent_name: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any] | None:
        async with self._sf() as session, session.begin():
            investigation = await session.get(InvestigationRow, investigation_id, with_for_update=True)
            if investigation is None or investigation.user_id != user_id:
                return None
            if investigation.status not in {
                InvestigationStatus.ANALYZING.value,
                InvestigationStatus.REWORKING.value,
            }:
                raise InvestigationConflict("Claims can only be submitted during analyzing or reworking")
            if idempotency_key is not None:
                alias = await session.get(ClaimSubmissionRow, sha256_text(f"{investigation_id}:{idempotency_key}"))
                existing = (
                    await session.get(ClaimRow, alias.claim_id) if alias else (await session.execute(select(ClaimRow).where(ClaimRow.investigation_id == investigation_id, ClaimRow.idempotency_key == idempotency_key))).scalar_one_or_none()
                )
                if existing is not None:
                    links = list((await session.execute(select(ClaimEvidenceRow).where(ClaimEvidenceRow.claim_id == existing.id))).scalars())
                    return self._claim_dict(existing, [self._claim_link_dict(link) for link in links])
            normalized = " ".join(request.text.casefold().split())
            existing = (
                await session.execute(
                    select(ClaimRow)
                    .where(
                        ClaimRow.investigation_id == investigation_id,
                        ClaimRow.competitor_id == request.competitor_id,
                        ClaimRow.dimension == request.dimension,
                        ClaimRow.claim_type == request.claim_type,
                        ClaimRow.normalized_text == normalized,
                    )
                    .order_by(ClaimRow.created_at)
                    .limit(1)
                )
            ).scalar_one_or_none()
            if existing is not None:
                if existing.status in {"rejected", "superseded"}:
                    raise InvestigationConflict("A retired Claim cannot be reintroduced without a revised proposition")
                if idempotency_key:
                    session.add(ClaimSubmissionRow(id=sha256_text(f"{investigation_id}:{idempotency_key}"), claim_id=existing.id))
                links = list((await session.execute(select(ClaimEvidenceRow).where(ClaimEvidenceRow.claim_id == existing.id))).scalars())
                return self._claim_dict(existing, [self._claim_link_dict(link) for link in links])
            binding_by_id = {binding.evidence_id: binding for binding in request.evidence_bindings}
            evidence_rows = list(
                (
                    await session.execute(
                        select(EvidenceRow).where(
                            EvidenceRow.investigation_id == investigation_id,
                            EvidenceRow.id.in_(binding_by_id),
                            EvidenceRow.status == EvidenceStatus.ACTIVE.value,
                        )
                    )
                ).scalars()
            )
            if len(evidence_rows) != len(binding_by_id):
                raise InvestigationConflict("Claim references missing or inactive evidence")
            if request.competitor_id:
                competitor = await session.get(CompetitorRow, request.competitor_id)
                if competitor is None or competitor.investigation_id != investigation_id:
                    raise InvestigationConflict("Claim competitor does not belong to investigation")
                if any(item.competitor_id and item.competitor_id != request.competitor_id for item in evidence_rows):
                    raise InvestigationConflict("Atomic Claim cannot borrow another competitor's Evidence")
            validated_links: list[tuple[EvidenceRow, Any, Any]] = []
            for evidence in evidence_rows:
                binding = binding_by_id[evidence.id]
                if evidence.snapshot_id is None:
                    raise InvestigationConflict("Claim references legacy Evidence without an immutable snapshot")
                snapshot = await session.get(EvidenceSnapshotRow, evidence.snapshot_id)
                if snapshot is None or snapshot.content_hash != binding.snapshot_sha256:
                    raise InvestigationConflict("Claim binding snapshot hash does not match Evidence")
                if request.material and binding.relation == ClaimEvidenceRelation.SUPPORTS and snapshot.extraction_method == "search_snippet":
                    raise InvestigationConflict("Material Claims cannot rely on a search snippet as supporting Evidence")
                try:
                    quote = locate_verbatim_quote(snapshot.content_text, binding.verbatim_quote)
                except EvidenceValidationError as exc:
                    raise InvestigationConflict(str(exc)) from exc
                if binding.quote_start is not None and binding.quote_start != quote.start:
                    raise InvestigationConflict("Claim binding quote_start does not match the immutable snapshot")
                if binding.quote_end is not None and binding.quote_end != quote.end:
                    raise InvestigationConflict("Claim binding quote_end does not match the immutable snapshot")
                validated_links.append((evidence, binding, quote))
            supporting = [item for item in validated_links if item[1].relation == ClaimEvidenceRelation.SUPPORTS]
            contradicting = [item for item in validated_links if item[1].relation == ClaimEvidenceRelation.CONTRADICTS]
            try:
                require_claim_values_in_quotes(request.text, [item[2].quote for item in supporting])
            except EvidenceValidationError as exc:
                raise InvestigationConflict(str(exc)) from exc
            count = independent_source_count(item[0].source_domain for item in supporting)
            if contradicting:
                status = ClaimStatus.CONTRADICTED if not supporting else ClaimStatus.UNCERTAIN
            else:
                status = ClaimStatus.UNCERTAIN
            pricing_dimension = request.claim_type == "pricing" or request.dimension.casefold() in {"pricing", "定价", "价格"}
            if pricing_dimension and not request.price_observations:
                raise InvestigationConflict("Pricing claims require at least one structured PriceObservation")
            claim = ClaimRow(
                id=self._id(),
                idempotency_key=idempotency_key,
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
            if idempotency_key:
                session.add(ClaimSubmissionRow(id=sha256_text(f"{investigation_id}:{idempotency_key}"), claim_id=claim.id))
            session.add_all(
                [
                    ClaimEvidenceRow(
                        claim_id=claim.id,
                        evidence_id=evidence.id,
                        relation=binding.relation.value,
                        support_score=evidence.credibility_score,
                        quoted_span=quote.quote,
                        quote_start=quote.start,
                        quote_end=quote.end,
                        snapshot_sha256=quote.snapshot_sha256,
                        validation_status="verified",
                        entailment_status="pending_audit",
                    )
                    for evidence, binding, quote in validated_links
                ]
            )
            evidence_by_id = {row.id: row for row in evidence_rows}
            for price in request.price_observations:
                evidence = evidence_by_id[price.evidence_id]
                official_domains: list[str] = []
                official_repositories: list[str] = []
                if evidence.competitor_id:
                    competitor = await session.get(CompetitorRow, evidence.competitor_id)
                    if competitor is not None:
                        official_domains = competitor.official_domains
                        official_repositories = competitor.official_repositories
                verified_official = is_official_source(evidence.source_url, official_domains, official_repositories)
                if price.official and not verified_official:
                    raise InvestigationConflict("Official PriceObservation domain is not approved in the research scope")
                try:
                    require_price_fields_in_quote(
                        amount=price.amount,
                        currency=price.currency,
                        billing_period=price.billing_period,
                        quote=price.verbatim_quote,
                    )
                    price_quote = locate_verbatim_quote(
                        (await session.get(EvidenceSnapshotRow, evidence.snapshot_id)).content_text,
                        price.verbatim_quote,
                    )
                except EvidenceValidationError as exc:
                    raise InvestigationConflict(str(exc)) from exc
                if price_quote.snapshot_sha256 != price.snapshot_sha256:
                    raise InvestigationConflict("PriceObservation snapshot hash does not match Evidence")
                session.add(
                    PriceObservationRow(
                        id=self._id(),
                        investigation_id=investigation_id,
                        claim_id=claim.id,
                        evidence_id=evidence.id,
                        plan_name=price.plan_name,
                        amount=price.amount,
                        currency=price.currency,
                        billing_period=price.billing_period,
                        billing_unit=price.billing_unit,
                        seat_minimum=price.seat_minimum,
                        region=price.region,
                        tax_included=price.tax_included,
                        promotion=price.promotion,
                        effective_at=price.effective_at,
                        official=verified_official,
                        verbatim_quote=price_quote.quote,
                        snapshot_sha256=price_quote.snapshot_sha256,
                    )
                )
            session.add(
                InvestigationEventRow(
                    investigation_id=investigation_id,
                    event_type="ci.claim.created",
                    stage=investigation.status,
                    payload={"claim_id": claim.id, "status": status.value, "independent_sources": count},
                )
            )
        return self._claim_dict(
            claim,
            [
                {
                    "evidence_id": evidence.id,
                    "relation": binding.relation.value,
                    "verbatim_quote": quote.quote,
                    "quote_start": quote.start,
                    "quote_end": quote.end,
                    "snapshot_sha256": quote.snapshot_sha256,
                    "validation_status": "verified",
                    "entailment_status": "pending_audit",
                }
                for evidence, binding, quote in validated_links
            ],
        )

    async def list_claims(self, investigation_id: str, *, user_id: str) -> list[dict[str, Any]] | None:
        if await self.get(investigation_id, user_id=user_id) is None:
            return None
        async with self._sf() as session:
            rows = list((await session.execute(select(ClaimRow).where(ClaimRow.investigation_id == investigation_id).order_by(ClaimRow.created_at))).scalars())
            links = list((await session.execute(select(ClaimEvidenceRow).join(ClaimRow, ClaimRow.id == ClaimEvidenceRow.claim_id).where(ClaimRow.investigation_id == investigation_id))).scalars())
            by_claim: dict[str, list[dict[str, Any]]] = {}
            for link in links:
                by_claim.setdefault(link.claim_id, []).append(self._claim_link_dict(link))
            from app.investigations.quality import claim_is_eligible

            issues = await self.list_audit_issues(investigation_id, user_id=user_id, status="open") or []
            result = [self._claim_dict(row, by_claim.get(row.id, [])) for row in rows]
            return [{**claim, "publication_eligible": claim_is_eligible(claim, issues)} for claim in result]

    async def list_price_observations(self, investigation_id: str, *, user_id: str) -> list[dict[str, Any]] | None:
        if await self.get(investigation_id, user_id=user_id) is None:
            return None
        async with self._sf() as session:
            rows = list((await session.execute(select(PriceObservationRow).where(PriceObservationRow.investigation_id == investigation_id).order_by(PriceObservationRow.created_at))).scalars())
            return [self._price_observation_dict(row) for row in rows]

    async def apply_audit_verdicts(
        self,
        investigation_id: str,
        verdicts: list[dict[str, Any]],
        *,
        user_id: str,
        auditor: str,
    ) -> None:
        allowed = {"entails", "partially_supports", "contradicts", "unrelated"}
        async with self._sf() as session, session.begin():
            investigation = await session.get(InvestigationRow, investigation_id, with_for_update=True)
            if investigation is None or investigation.user_id != user_id:
                raise LookupError("Investigation not found")
            if investigation.status != InvestigationStatus.AUDITING.value:
                raise InvestigationConflict("Binding verdicts can only be submitted during auditing")
            active_ids = select(ClaimRow.id).where(ClaimRow.investigation_id == investigation_id, ClaimRow.status.not_in(["superseded", "rejected"]))
            await session.execute(update(ClaimEvidenceRow).where(ClaimEvidenceRow.claim_id.in_(active_ids)).values(entailment_status="pending_audit"))
            for verdict in verdicts:
                status = str(verdict.get("verdict") or "")
                if status not in allowed:
                    continue
                link = await session.get(
                    ClaimEvidenceRow,
                    (
                        str(verdict.get("claim_id") or ""),
                        str(verdict.get("evidence_id") or ""),
                        str(verdict.get("relation") or ClaimEvidenceRelation.SUPPORTS.value),
                    ),
                    with_for_update=True,
                )
                if link is None:
                    continue
                claim = await session.get(ClaimRow, link.claim_id)
                if claim is None or claim.investigation_id != investigation_id:
                    continue
                link.entailment_status = status
                link.auditor_status = auditor
                if link.relation == ClaimEvidenceRelation.CONTEXT.value:
                    if status == "entails":
                        link.relation = ClaimEvidenceRelation.SUPPORTS.value
                    elif status == "contradicts":
                        link.relation = ClaimEvidenceRelation.CONTRADICTS.value

            claims = list((await session.execute(select(ClaimRow).where(ClaimRow.investigation_id == investigation_id))).scalars())
            for claim in claims:
                if claim.status in {"superseded", "rejected"}:
                    continue
                rows = list((await session.execute(select(ClaimEvidenceRow, EvidenceRow).join(EvidenceRow, EvidenceRow.id == ClaimEvidenceRow.evidence_id).where(ClaimEvidenceRow.claim_id == claim.id))).all())
                supporting = [
                    (link, evidence) for link, evidence in rows if evidence.status == "active" and link.validation_status == "verified" and link.relation == ClaimEvidenceRelation.SUPPORTS.value and link.entailment_status == "entails"
                ]
                competitor = await session.get(CompetitorRow, claim.competitor_id) if claim.competitor_id else None
                sources = []
                for _, evidence in supporting:
                    snapshot = await session.get(EvidenceSnapshotRow, evidence.snapshot_id) if evidence.snapshot_id else None
                    if snapshot is None or snapshot.extraction_method == "search_snippet":
                        continue
                    official = bool(competitor and evidence.competitor_id == competitor.id and is_official_source(evidence.source_url, competitor.official_domains, competitor.official_repositories))
                    sources.append({"domain": urlsplit(evidence.source_url).hostname or "", "official": official})
                has_contradiction = any(link.entailment_status == "contradicts" or link.relation == ClaimEvidenceRelation.CONTRADICTS.value for link, _ in rows)
                count = independent_source_count(source["domain"] for source in sources)
                claim.independent_source_count = count
                if has_contradiction:
                    claim.status = ClaimStatus.CONTRADICTED.value if count == 0 else ClaimStatus.UNCERTAIN.value
                    claim.support_basis = "unverified"
                else:
                    claim.support_basis = assess_support(claim.claim_type, claim.text, sources)
                    claim.status = (ClaimStatus.SUPPORTED if claim.support_basis != "unverified" else ClaimStatus.UNCERTAIN).value

    async def retrieve_context(
        self,
        investigation_id: str,
        *,
        user_id: str,
        query: str,
        limit: int = 12,
        competitor_id: str | None = None,
    ) -> list[dict[str, Any]] | None:
        if await self.get(investigation_id, user_id=user_id) is None:
            return None
        async with self._sf() as session:
            rows = list(
                (
                    await session.execute(
                        select(
                            EvidenceChunkRow,
                            EvidenceRow.id,
                            EvidenceRow.source_domain,
                            EvidenceRow.source_type,
                            EvidenceSnapshotRow.content_hash,
                        )
                        .join(EvidenceSnapshotRow, EvidenceSnapshotRow.id == EvidenceChunkRow.snapshot_id)
                        .join(EvidenceRow, EvidenceRow.snapshot_id == EvidenceSnapshotRow.id)
                        .where(EvidenceRow.investigation_id == investigation_id, EvidenceRow.status == EvidenceStatus.ACTIVE.value)
                    )
                ).all()
            )
        chunks = [
            {
                "id": row.id,
                "evidence_id": evidence_id,
                "source_domain": source_domain,
                "source_type": source_type,
                "ordinal": row.ordinal,
                "char_start": row.char_start,
                "char_end": row.char_end,
                "content": row.content,
                "content_hash": row.content_hash,
                "token_estimate": row.token_estimate,
                "embedding": row.embedding,
                "snapshot_sha256": snapshot_sha256,
            }
            for row, evidence_id, source_domain, source_type, snapshot_sha256 in rows
        ]
        query_embedding = None
        if self._embedding_provider is not None:
            query_embedding = (await self._embedding_provider.embed_texts([query]))[0]
        if competitor_id is not None:
            allowed = {item["id"] for item in await self.list_evidence(investigation_id, user_id=user_id) or [] if item.get("competitor_id") == competitor_id}
            chunks = [chunk for chunk in chunks if chunk["evidence_id"] in allowed]
        ranked = rank_chunks(query, chunks, limit=limit, query_embedding=query_embedding)
        return [{key: value for key, value in chunk.items() if key != "embedding"} for chunk in ranked]

    async def supplement_claim_evidence(
        self,
        investigation_id: str,
        claim_id: str,
        evidence_ids: list[str],
        *,
        user_id: str,
        relation: ClaimEvidenceRelation = ClaimEvidenceRelation.SUPPORTS,
    ) -> dict[str, Any] | None:
        async with self._sf() as session, session.begin():
            investigation = await session.get(InvestigationRow, investigation_id, with_for_update=True)
            if investigation is None or investigation.user_id != user_id:
                return None
            if investigation.status != InvestigationStatus.REWORKING.value:
                raise InvestigationConflict("Claim evidence can only be supplemented during reworking")
            claim = await session.get(ClaimRow, claim_id, with_for_update=True)
            if claim is None or claim.investigation_id != investigation_id:
                raise InvestigationConflict("Claim not found in investigation")
            evidence_rows = list(
                (
                    await session.execute(
                        select(EvidenceRow).where(
                            EvidenceRow.investigation_id == investigation_id,
                            EvidenceRow.id.in_(evidence_ids),
                            EvidenceRow.status == EvidenceStatus.ACTIVE.value,
                        )
                    )
                ).scalars()
            )
            if len(evidence_rows) != len(set(evidence_ids)):
                raise InvestigationConflict("Claim supplement references missing or inactive evidence")
            existing_ids = set((await session.execute(select(ClaimEvidenceRow.evidence_id).where(ClaimEvidenceRow.claim_id == claim_id))).scalars())
            for row in evidence_rows:
                if row.id in existing_ids:
                    continue
                if row.snapshot_id is None:
                    raise InvestigationConflict("Claim supplement references Evidence without an immutable snapshot")
                snapshot = await session.get(EvidenceSnapshotRow, row.snapshot_id)
                if snapshot is None:
                    raise InvestigationConflict("Evidence snapshot is missing")
                try:
                    quote = locate_verbatim_quote(snapshot.content_text, row.excerpt)
                except EvidenceValidationError as exc:
                    raise InvestigationConflict(str(exc)) from exc
                session.add(
                    ClaimEvidenceRow(
                        claim_id=claim.id,
                        evidence_id=row.id,
                        relation=relation.value,
                        support_score=row.credibility_score,
                        quoted_span=quote.quote,
                        quote_start=quote.start,
                        quote_end=quote.end,
                        snapshot_sha256=quote.snapshot_sha256,
                        validation_status="verified",
                        entailment_status="pending_audit",
                    )
                )
            await session.flush()
            all_evidence = list(
                (
                    await session.execute(
                        select(EvidenceRow).join(ClaimEvidenceRow, ClaimEvidenceRow.evidence_id == EvidenceRow.id).where(ClaimEvidenceRow.claim_id == claim_id, ClaimEvidenceRow.relation == ClaimEvidenceRelation.SUPPORTS.value)
                    )
                ).scalars()
            )
            count = independent_source_count(row.source_domain for row in all_evidence)
            claim.independent_source_count = count
            claim.status = ClaimStatus.UNCERTAIN.value
            links = list((await session.execute(select(ClaimEvidenceRow).where(ClaimEvidenceRow.claim_id == claim_id))).scalars())
        return self._claim_dict(claim, [self._claim_link_dict(link) for link in links])

    async def replace_audit_issues(
        self,
        investigation_id: str,
        issues: list[dict[str, Any]],
        *,
        user_id: str,
        raised_by: str,
    ) -> list[dict[str, Any]] | None:
        async with self._sf() as session, session.begin():
            investigation = await session.get(InvestigationRow, investigation_id, with_for_update=True)
            if investigation is None or investigation.user_id != user_id:
                return None
            if investigation.status != InvestigationStatus.AUDITING.value:
                raise InvestigationConflict("Audit issues can only be submitted during auditing")
            open_rows = list(
                (
                    await session.execute(
                        select(AuditIssueRow).where(
                            AuditIssueRow.investigation_id == investigation_id,
                            AuditIssueRow.status == "open",
                        )
                    )
                ).scalars()
            )
            existing_by_key = {(row.claim_id, row.section_id, row.rule): row for row in open_rows}
            created: list[AuditIssueRow] = []
            for issue in issues:
                key = (issue.get("claim_id"), issue.get("section_id"), str(issue.get("rule") or "agent_audit")[:64])
                if key in existing_by_key:
                    continue
                row = AuditIssueRow(
                    id=self._id(),
                    investigation_id=investigation_id,
                    claim_id=issue.get("claim_id"),
                    section_id=issue.get("section_id"),
                    severity=str(issue.get("severity") or "warning")[:16],
                    rule=str(issue.get("rule") or "agent_audit")[:64],
                    reason=str(issue.get("reason") or "Unspecified audit issue")[:20_000],
                    required_action=str(issue.get("required_action") or "Collect stronger evidence")[:20_000],
                    status="open",
                    raised_by=raised_by,
                )
                session.add(row)
                created.append(row)
                existing_by_key[key] = row
                session.add(
                    InvestigationEventRow(
                        investigation_id=investigation_id,
                        event_type="ci.audit.issue",
                        stage="auditing",
                        payload={"issue_id": row.id, "claim_id": row.claim_id, "severity": row.severity, "rule": row.rule},
                    )
                )
        return [self._audit_issue_dict(row) for row in [*open_rows, *created]]

    async def resolve_audit_issues(self, investigation_id: str, resolutions: list[dict], *, user_id: str) -> None:
        async with self._sf() as session, session.begin():
            investigation = await session.get(InvestigationRow, investigation_id, with_for_update=True)
            if investigation is None or investigation.user_id != user_id:
                raise LookupError("Investigation not found")
            if investigation.status != "auditing":
                raise InvestigationConflict("Issue resolution requires auditing stage")
            for resolution in resolutions:
                issue = await session.get(AuditIssueRow, str(resolution.get("issue_id", "")))
                if issue is None or issue.investigation_id != investigation_id or issue.status != "open":
                    continue
                claim = await session.get(ClaimRow, issue.claim_id) if issue.claim_id else None
                if claim is None or resolution.get("claim_version") != claim.version or not str(resolution.get("reason", "")).strip():
                    continue
                if claim.status not in {"supported", "superseded", "rejected"}:
                    continue
                issue.status = "resolved"
                issue.resolved_by = "evidence-auditor"
                session.add(InvestigationEventRow(investigation_id=investigation_id, stage="auditing", event_type="ci.audit.resolved", payload={"issue_id": issue.id, "claim_version": claim.version, "reason": resolution["reason"]}))

    async def retire_claim(self, investigation_id: str, claim_id: str, *, user_id: str, expected_version: int, action: str, replacement_ids: list[str]) -> None:
        if action not in {"revise", "split", "reject"} or (action in {"revise", "split"} and not replacement_ids):
            raise InvestigationConflict("Invalid Claim revision action")
        async with self._sf() as session, session.begin():
            investigation = await session.get(InvestigationRow, investigation_id, with_for_update=True)
            if investigation is None or investigation.user_id != user_id:
                raise LookupError("Investigation not found")
            if investigation.status != "reworking":
                raise InvestigationConflict("Claim revision requires reworking stage")
            claim = await session.get(ClaimRow, claim_id)
            if claim is None or claim.investigation_id != investigation_id or claim.version != expected_version:
                raise InvestigationConflict("Claim version changed")
            if claim.status in {"superseded", "rejected"}:
                return
            for cid in replacement_ids:
                replacement = await session.get(ClaimRow, cid)
                if replacement is None or replacement.investigation_id != investigation_id or replacement.competitor_id != claim.competitor_id or cid == claim_id:
                    raise InvestigationConflict("Invalid replacement Claim")
            claim.status = "rejected" if action == "reject" else "superseded"
            session.add(
                InvestigationEventRow(investigation_id=investigation_id, stage="reworking", event_type="ci.claim.revised", payload={"claim_id": claim_id, "version": claim.version, "action": action, "replacement_ids": replacement_ids})
            )

    async def begin_audit_rework(self, investigation_id: str, *, user_id: str, issue_ids: list[str]) -> dict[str, Any] | None:
        async with self._sf() as session, session.begin():
            investigation = await session.get(InvestigationRow, investigation_id, with_for_update=True)
            if investigation is None or investigation.user_id != user_id:
                return None
            if investigation.rework_round >= 2:
                raise InvestigationConflict("Maximum audit rework rounds reached")
            require_transition(InvestigationStatus(investigation.status), InvestigationStatus.REWORKING)
            investigation.status = InvestigationStatus.REWORKING.value
            investigation.rework_round += 1
            investigation.updated_at = datetime.now(UTC)
            session.add(
                InvestigationEventRow(
                    investigation_id=investigation_id,
                    event_type="ci.rework.requested",
                    stage="reworking",
                    payload={"issue_ids": issue_ids, "round": investigation.rework_round, "source": "evidence_audit"},
                )
            )
        return await self.get(investigation_id, user_id=user_id)

    async def retry_failed(self, investigation_id: str, *, user_id: str, idempotency_key: str) -> dict[str, Any] | None:
        async with self._sf() as session, session.begin():
            investigation = await session.get(InvestigationRow, investigation_id, with_for_update=True)
            if investigation is None or investigation.user_id != user_id:
                return None
            if investigation.status != InvestigationStatus.FAILED.value:
                raise InvestigationConflict("Only failed Investigations can start a recovery round")
            scope = (await session.execute(select(ScopeRow).where(ScopeRow.investigation_id == investigation_id))).scalar_one()
            if scope.approved_at is None:
                raise InvestigationConflict("Failed Planning must be recreated; only approved execution can be retried")
            if investigation.failure_retry_count >= 2:
                raise InvestigationConflict("Maximum technical recovery attempts reached")
            if investigation.token_used >= investigation.token_budget:
                raise InvestigationBudgetExceeded("Investigation token budget is already exhausted")
            failed_stage = (
                await session.execute(
                    select(StageAttemptRow.stage)
                    .join(WorkflowRunRow, WorkflowRunRow.id == StageAttemptRow.workflow_run_id)
                    .where(
                        WorkflowRunRow.investigation_id == investigation_id,
                        StageAttemptRow.status == "failed",
                    )
                    .order_by(StageAttemptRow.created_at.desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            resume_status = {
                "collecting": InvestigationStatus.COLLECTING,
                "reworking": InvestigationStatus.REWORKING,
                "analyzing": InvestigationStatus.ANALYZING,
                "auditing": InvestigationStatus.AUDITING,
                "synthesizing": InvestigationStatus.SYNTHESIZING,
            }.get(failed_stage or "", InvestigationStatus.COLLECTING)
            investigation.failure_retry_count += 1
            investigation.status = resume_status.value
            investigation.error = None
            investigation.updated_at = datetime.now(UTC)
            session.add(
                InvestigationEventRow(
                    investigation_id=investigation_id,
                    event_type="ci.rework.requested",
                    stage=resume_status.value,
                    payload={
                        "source": "failed_workflow_recovery",
                        "recovery_attempt": investigation.failure_retry_count,
                        "resume_stage": resume_status.value,
                        "idempotency_key": idempotency_key,
                    },
                )
            )
        return await self.get(investigation_id, user_id=user_id)

    async def list_audit_issues(self, investigation_id: str, *, user_id: str, status: str | None = None) -> list[dict[str, Any]] | None:
        if await self.get(investigation_id, user_id=user_id) is None:
            return None
        async with self._sf() as session:
            statement = select(AuditIssueRow).where(AuditIssueRow.investigation_id == investigation_id)
            if status is not None:
                statement = statement.where(AuditIssueRow.status == status)
            rows = list((await session.execute(statement.order_by(AuditIssueRow.created_at))).scalars())
            return [self._audit_issue_dict(row) for row in rows]

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

    async def create_report(
        self,
        investigation_id: str,
        *,
        structured_data: dict[str, Any],
        rendered_markdown: str,
        user_id: str,
        allow_failed_partial: bool = False,
    ) -> dict[str, Any] | None:
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
                        id=self._id(),
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
                if not (allow_failed_partial and current == InvestigationStatus.FAILED):
                    require_transition(current, InvestigationStatus.AWAITING_PUBLISH_APPROVAL)
                investigation.status = InvestigationStatus.AWAITING_PUBLISH_APPROVAL.value
            session.add(
                InvestigationEventRow(
                    investigation_id=investigation_id,
                    event_type="ci.report.ready_for_review",
                    stage="awaiting_publish_approval",
                    payload={
                        "report_id": report.id,
                        "version": report.version,
                        "partial": bool(structured_data.get("partial")),
                    },
                )
            )
        return await self.latest_report(investigation_id, user_id=user_id)

    async def record_export(
        self,
        investigation_id: str,
        *,
        report_id: str,
        export_format: str,
        object_ref: str,
        content_hash: str,
        user_id: str,
    ) -> dict[str, Any] | None:
        async with self._sf() as session, session.begin():
            investigation = await session.get(InvestigationRow, investigation_id)
            if investigation is None or investigation.user_id != user_id:
                return None
            report = await session.get(ReportRow, report_id)
            if report is None or report.investigation_id != investigation_id:
                raise InvestigationConflict("Export report does not belong to Investigation")
            existing = (
                await session.execute(
                    select(ExportRow).where(
                        ExportRow.report_id == report_id,
                        ExportRow.format == export_format,
                        ExportRow.content_hash == content_hash,
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                existing = ExportRow(
                    id=self._id(),
                    investigation_id=investigation_id,
                    report_id=report_id,
                    format=export_format,
                    status="ready",
                    object_ref=object_ref,
                    content_hash=content_hash,
                )
                session.add(existing)
            return {
                "id": existing.id,
                "format": existing.format,
                "status": existing.status,
                "object_ref": existing.object_ref,
                "content_hash": existing.content_hash,
            }

    async def approve_report(self, investigation_id: str, report_version: int, *, user_id: str, idempotency_key: str) -> dict[str, Any] | None:
        async with self._sf() as session, session.begin():
            investigation = await session.get(InvestigationRow, investigation_id, with_for_update=True)
            if investigation is None or investigation.user_id != user_id:
                return None
            report = (await session.execute(select(ReportRow).where(ReportRow.investigation_id == investigation_id, ReportRow.version == report_version).with_for_update())).scalar_one_or_none()
            if report is None:
                raise InvestigationConflict("Report version not found")
            if report.status != "published":
                latest = (await session.execute(select(func.max(ReportRow.version)).where(ReportRow.investigation_id == investigation_id))).scalar_one()
                if latest != report_version or report.status != "review":
                    raise InvestigationConflict("Only the latest reviewed report can be published")
                data = report.structured_data or {}
                if not data.get("partial") and data.get("schema_version") != "competitive-report-v2":
                    raise InvestigationConflict("Legacy report requires regeneration with publication quality gates")
                from app.investigations.quality import claim_is_eligible, coverage_cells

                issue_rows = list((await session.execute(select(AuditIssueRow).where(AuditIssueRow.investigation_id == investigation_id, AuditIssueRow.status == "open"))).scalars())
                issues = [self._audit_issue_dict(issue) for issue in issue_rows]
                current_claims = []
                for cid, version in data.get("claim_versions", {}).items():
                    claim = await session.get(ClaimRow, cid)
                    if claim is None or claim.investigation_id != investigation_id or claim.version != version or not claim_is_eligible(self._claim_dict(claim, []), issues):
                        raise InvestigationConflict("Report Claim eligibility changed; regenerate before publication")
                    expected_basis = data.get("claim_support_basis", {}).get(cid, "corroborated")
                    if expected_basis != claim.support_basis:
                        raise InvestigationConflict("Report source attribution changed; regenerate before publication")
                    current_claims.append(self._claim_dict(claim, []))
                if not data.get("partial"):
                    if data.get("quality_policy_version") == POLICY_VERSION:
                        scope = (await session.execute(select(ScopeRow).where(ScopeRow.investigation_id == investigation_id))).scalar_one()
                        competitors = list((await session.execute(select(CompetitorRow).where(CompetitorRow.investigation_id == investigation_id))).scalars())
                        cells = coverage_cells([{"id": item.id, "name": item.canonical_name} for item in competitors], scope.dimensions, current_claims, issues)
                        if completion_state(self._serialize(investigation, scope, competitors), cells, current_claims, issues) == "incomplete":
                            raise InvestigationConflict("Core research requirements remain incomplete")
                    elif not data.get("claim_versions") or any(cell.get("status") != "covered" for cell in data.get("coverage", [])):
                        raise InvestigationConflict("Incomplete research cannot publish as a complete report")
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
                "required_dimensions": scope.required_dimensions,
                "official_domains": {item.canonical_name: item.official_domains for item in competitors if item.official_domains},
                "official_repositories": {item.canonical_name: item.official_repositories for item in competitors if item.official_repositories},
                "version": scope.version,
                "approved_at": scope.approved_at,
            },
            "rework_round": row.rework_round,
            "failure_retry_count": row.failure_retry_count,
            "token_used": row.token_used,
            "token_reserved": row.token_reserved,
            "token_budget": row.token_budget,
            "deadline_at": row.deadline_at,
            "created_at": row.created_at,
            "updated_at": row.updated_at,
        }

    @staticmethod
    def _evidence_dict(row: EvidenceRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "snapshot_id": row.snapshot_id,
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
    def _claim_dict(row: ClaimRow, evidence_bindings: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "id": row.id,
            "investigation_id": row.investigation_id,
            "competitor_id": row.competitor_id,
            "dimension": row.dimension,
            "text": row.text,
            "material": row.material,
            "claim_type": row.claim_type,
            "version": row.version,
            "evidence_ids": [binding["evidence_id"] for binding in evidence_bindings],
            "evidence_bindings": evidence_bindings,
            "status": row.status,
            "independent_source_count": row.independent_source_count,
            "support_basis": row.support_basis,
            "display_text": claim_display_text({"text": row.text, "support_basis": row.support_basis}),
        }

    @staticmethod
    def _claim_link_dict(row: ClaimEvidenceRow) -> dict[str, Any]:
        return {
            "evidence_id": row.evidence_id,
            "relation": row.relation,
            "verbatim_quote": row.quoted_span,
            "quote_start": row.quote_start,
            "quote_end": row.quote_end,
            "snapshot_sha256": row.snapshot_sha256,
            "validation_status": row.validation_status,
            "entailment_status": row.entailment_status,
        }

    @staticmethod
    def _price_observation_dict(row: PriceObservationRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "investigation_id": row.investigation_id,
            "claim_id": row.claim_id,
            "evidence_id": row.evidence_id,
            "plan_name": row.plan_name,
            "amount": row.amount,
            "currency": row.currency,
            "billing_period": row.billing_period,
            "billing_unit": row.billing_unit,
            "seat_minimum": row.seat_minimum,
            "region": row.region,
            "tax_included": row.tax_included,
            "promotion": row.promotion,
            "effective_at": row.effective_at,
            "official": row.official,
            "verbatim_quote": row.verbatim_quote,
            "snapshot_sha256": row.snapshot_sha256,
            "created_at": row.created_at,
        }

    @staticmethod
    def _audit_issue_dict(row: AuditIssueRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "investigation_id": row.investigation_id,
            "claim_id": row.claim_id,
            "section_id": row.section_id,
            "severity": row.severity,
            "rule": row.rule,
            "reason": row.reason,
            "required_action": row.required_action,
            "status": row.status,
            "raised_by": row.raised_by,
            "resolved_by": row.resolved_by,
            "blocking": issue_is_blocking({"status": row.status, "severity": row.severity, "rule": row.rule}),
            "created_at": row.created_at,
        }
