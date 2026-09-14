from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.investigations.persistence.models import InvestigationRow, StageAttemptRow, StageItemRow, WorkflowRunRow
from app.investigations.protocols import AgentReceipt, DomainSubmission, StageName, StageTask


class WorkflowLeaseLost(RuntimeError):
    pass


class StageRetryExhausted(RuntimeError):
    pass


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class OrchestrationRepository:
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._sf = session_factory

    @staticmethod
    def _id() -> str:
        return uuid.uuid4().hex

    async def ensure_workflow(self, investigation_id: str, *, user_id: str, idempotency_key: str) -> dict[str, Any]:
        async with self._sf() as session:
            investigation = await session.get(InvestigationRow, investigation_id)
            if investigation is None or investigation.user_id != user_id:
                raise LookupError("Investigation not found")
            existing = (await session.execute(select(WorkflowRunRow).where(WorkflowRunRow.idempotency_key == idempotency_key))).scalar_one_or_none()
            if existing is not None:
                return self._workflow_dict(existing)
            row = WorkflowRunRow(
                id=self._id(),
                investigation_id=investigation_id,
                status="queued",
                idempotency_key=idempotency_key,
            )
            session.add(row)
            try:
                await session.commit()
            except IntegrityError:
                await session.rollback()
                existing = (await session.execute(select(WorkflowRunRow).where(WorkflowRunRow.idempotency_key == idempotency_key))).scalar_one()
                return self._workflow_dict(existing)
            await session.refresh(row)
            return self._workflow_dict(row)

    async def claim_workflow(
        self,
        workflow_run_id: str,
        *,
        owner_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> dict[str, Any] | None:
        now = now or datetime.now(UTC)
        async with self._sf() as session, session.begin():
            row = await session.get(WorkflowRunRow, workflow_run_id, with_for_update=True)
            if row is None or row.status not in {"queued", "running"}:
                return None
            if row.owner_id not in {None, owner_id} and row.lease_expires_at is not None and _as_utc(row.lease_expires_at) > _as_utc(now):
                return None
            row.owner_id = owner_id
            row.lease_expires_at = now + timedelta(seconds=lease_seconds)
            row.status = "running"
            row.started_at = row.started_at or now
            return self._workflow_dict(row)

    async def renew_workflow_lease(
        self,
        workflow_run_id: str,
        *,
        owner_id: str,
        lease_seconds: int,
        now: datetime | None = None,
    ) -> bool:
        now = now or datetime.now(UTC)
        async with self._sf() as session, session.begin():
            row = await session.get(WorkflowRunRow, workflow_run_id, with_for_update=True)
            if row is None or row.status != "running" or row.owner_id != owner_id:
                return False
            row.lease_expires_at = now + timedelta(seconds=lease_seconds)
            return True

    async def finalize_workflow(self, workflow_run_id: str, *, owner_id: str, succeeded: bool) -> bool:
        async with self._sf() as session, session.begin():
            row = await session.get(WorkflowRunRow, workflow_run_id, with_for_update=True)
            if row is None or row.owner_id != owner_id:
                return False
            row.status = "completed" if succeeded else "failed"
            row.finished_at = datetime.now(UTC)
            row.lease_expires_at = None
            return True

    async def recoverable_workflows(self, *, now: datetime | None = None, limit: int = 100) -> list[dict[str, Any]]:
        now = now or datetime.now(UTC)
        async with self._sf() as session:
            rows = list(
                (
                    await session.execute(
                        select(WorkflowRunRow, InvestigationRow.user_id)
                        .join(InvestigationRow, InvestigationRow.id == WorkflowRunRow.investigation_id)
                        .where(
                            WorkflowRunRow.status.in_(("queued", "running")),
                            or_(WorkflowRunRow.owner_id.is_(None), WorkflowRunRow.lease_expires_at.is_(None), WorkflowRunRow.lease_expires_at <= now),
                        )
                        .order_by(WorkflowRunRow.started_at.asc().nullsfirst(), WorkflowRunRow.id.asc())
                        .limit(limit)
                    )
                ).all()
            )
            return [{**self._workflow_dict(row), "user_id": user_id} for row, user_id in rows]

    async def start_stage(
        self,
        workflow_run_id: str,
        *,
        owner_id: str,
        stage: StageName,
        tasks: list[StageTask],
        run_id: str | None = None,
        durable_batch_id: str | None = None,
    ) -> dict[str, Any]:
        now = datetime.now(UTC)
        async with self._sf() as session, session.begin():
            workflow = await session.get(WorkflowRunRow, workflow_run_id, with_for_update=True)
            if workflow is None or workflow.status != "running" or workflow.owner_id != owner_id:
                raise WorkflowLeaseLost("Workflow lease is not owned by this orchestrator")
            latest_row = (await session.execute(select(StageAttemptRow).where(StageAttemptRow.workflow_run_id == workflow_run_id, StageAttemptRow.stage == stage.value).order_by(StageAttemptRow.attempt.desc()).limit(1))).scalar_one_or_none()
            if latest_row is not None and latest_row.status in {"running", "completed"}:
                existing_items = list((await session.execute(select(StageItemRow).where(StageItemRow.stage_attempt_id == latest_row.id))).scalars())
                if {row.task_id for row in existing_items} == {task.task_id for task in tasks}:
                    return self._attempt_dict(latest_row, resumed=True)
            if latest_row is not None and latest_row.status == "failed" and latest_row.attempt >= 3:
                raise StageRetryExhausted(f"Stage {stage.value} exhausted 3 attempts")
            latest = (
                await session.execute(
                    select(func.max(StageAttemptRow.attempt)).where(
                        StageAttemptRow.workflow_run_id == workflow_run_id,
                        StageAttemptRow.stage == stage.value,
                    )
                )
            ).scalar_one_or_none() or 0
            attempt = StageAttemptRow(
                id=self._id(),
                workflow_run_id=workflow_run_id,
                stage=stage.value,
                attempt=latest + 1,
                status="running",
                run_id=run_id,
                task_id=durable_batch_id,
                created_at=now,
            )
            session.add(attempt)
            await session.flush()
            for task in tasks:
                session.add(
                    StageItemRow(
                        id=self._id(),
                        task_id=task.task_id,
                        workflow_run_id=workflow_run_id,
                        stage_attempt_id=attempt.id,
                        stage=stage.value,
                        item_key=task.item_key,
                        role=task.role,
                        status="pending",
                        attempt=0,
                        max_attempts=3,
                        task_envelope=task.model_dump(mode="json"),
                        durable_batch_id=durable_batch_id,
                        created_at=now,
                        updated_at=now,
                    )
                )
            return self._attempt_dict(attempt, resumed=False)

    async def bind_run(self, stage_attempt_id: str, *, run_id: str) -> None:
        async with self._sf() as session, session.begin():
            attempt = await session.get(StageAttemptRow, stage_attempt_id, with_for_update=True)
            if attempt is None:
                raise LookupError("Stage attempt not found")
            attempt.run_id = run_id

    async def bind_durable_batch(self, stage_attempt_id: str, *, batch_id: str, item_ids_by_key: dict[str, str]) -> None:
        async with self._sf() as session, session.begin():
            attempt = await session.get(StageAttemptRow, stage_attempt_id, with_for_update=True)
            if attempt is None:
                raise LookupError("Stage attempt not found")
            attempt.task_id = batch_id
            rows = list((await session.execute(select(StageItemRow).where(StageItemRow.stage_attempt_id == stage_attempt_id))).scalars())
            for row in rows:
                row.durable_batch_id = batch_id
                row.durable_batch_item_id = item_ids_by_key.get(row.item_key)

    async def update_stage_item(
        self,
        stage_attempt_id: str,
        *,
        item_key: str,
        status: str,
        attempt: int,
        submission: DomainSubmission | None = None,
        receipt: AgentReceipt | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        async with self._sf() as session, session.begin():
            row = (await session.execute(select(StageItemRow).where(StageItemRow.stage_attempt_id == stage_attempt_id, StageItemRow.item_key == item_key).with_for_update())).scalar_one()
            row.status = status
            row.attempt = attempt
            row.submission = submission.model_dump(mode="json") if submission else None
            row.receipt = receipt.model_dump(mode="json") if receipt else None
            row.error = error
            row.started_at = row.started_at or datetime.now(UTC)
            if status in {"succeeded", "failed", "rejected", "cancelled"}:
                row.completed_at = datetime.now(UTC)
            return self._item_dict(row)

    async def get_stage_submission(self, stage_attempt_id: str, *, item_key: str) -> DomainSubmission | None:
        async with self._sf() as session:
            row = (
                await session.execute(
                    select(StageItemRow).where(
                        StageItemRow.stage_attempt_id == stage_attempt_id,
                        StageItemRow.item_key == item_key,
                    )
                )
            ).scalar_one_or_none()
            if row is None or row.submission is None:
                return None
            return DomainSubmission.model_validate(row.submission)

    async def finish_stage(self, stage_attempt_id: str, *, succeeded: bool, error: str | None = None) -> None:
        async with self._sf() as session, session.begin():
            row = await session.get(StageAttemptRow, stage_attempt_id, with_for_update=True)
            if row is None:
                raise LookupError("Stage attempt not found")
            row.status = "completed" if succeeded else "failed"
            row.error = error

    async def list_stage_items(self, investigation_id: str, *, user_id: str) -> list[dict[str, Any]] | None:
        async with self._sf() as session:
            investigation = await session.get(InvestigationRow, investigation_id)
            if investigation is None or investigation.user_id != user_id:
                return None
            rows = list(
                (
                    await session.execute(
                        select(StageItemRow).join(WorkflowRunRow, WorkflowRunRow.id == StageItemRow.workflow_run_id).where(WorkflowRunRow.investigation_id == investigation_id).order_by(StageItemRow.created_at, StageItemRow.item_key)
                    )
                ).scalars()
            )
            return [self._item_dict(row) for row in rows]

    @staticmethod
    def _workflow_dict(row: WorkflowRunRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "investigation_id": row.investigation_id,
            "status": row.status,
            "owner_id": row.owner_id,
            "lease_expires_at": row.lease_expires_at,
            "idempotency_key": row.idempotency_key,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
        }

    @staticmethod
    def _item_dict(row: StageItemRow) -> dict[str, Any]:
        return {
            "id": row.id,
            "task_id": row.task_id,
            "workflow_run_id": row.workflow_run_id,
            "stage_attempt_id": row.stage_attempt_id,
            "stage": row.stage,
            "item_key": row.item_key,
            "role": row.role,
            "status": row.status,
            "attempt": row.attempt,
            "max_attempts": row.max_attempts,
            "task_envelope": row.task_envelope,
            "submission": row.submission,
            "receipt": row.receipt,
            "durable_batch_id": row.durable_batch_id,
            "durable_batch_item_id": row.durable_batch_item_id,
            "error": row.error,
            "started_at": row.started_at,
            "completed_at": row.completed_at,
        }

    @staticmethod
    def _attempt_dict(row: StageAttemptRow, *, resumed: bool) -> dict[str, Any]:
        return {
            "id": row.id,
            "workflow_run_id": row.workflow_run_id,
            "stage": row.stage,
            "attempt": row.attempt,
            "status": row.status,
            "run_id": row.run_id,
            "durable_batch_id": row.task_id,
            "resumed": resumed,
        }
