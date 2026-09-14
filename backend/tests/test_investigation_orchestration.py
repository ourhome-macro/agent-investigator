from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.investigations.contracts import InvestigationCreate, ResearchScope
from app.investigations.orchestration_repository import OrchestrationRepository, WorkflowLeaseLost
from app.investigations.persistence import upgrade_investigation_schema
from app.investigations.protocols import AgentReceipt, ReceiptStatus, StageName, StageTask, SubmissionKind
from app.investigations.repository import InvestigationRepository


async def _repositories(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'orchestrator.db'}")
    await upgrade_investigation_schema(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, InvestigationRepository(factory), OrchestrationRepository(factory)


@pytest.mark.asyncio
async def test_workflow_lease_fences_competing_orchestrators(tmp_path) -> None:
    engine, investigations, orchestration = await _repositories(tmp_path)
    try:
        investigation = await investigations.create(
            InvestigationCreate(
                title="Acme vs Beta",
                brief="Compare Acme and Beta for a product decision.",
                scope=ResearchScope(competitors=["Acme", "Beta"]),
            ),
            user_id="user-1",
        )
        workflow = await orchestration.ensure_workflow(investigation["id"], user_id="user-1", idempotency_key=f"{investigation['id']}:v1")
        now = datetime.now(UTC)
        assert await orchestration.claim_workflow(workflow["id"], owner_id="worker-a", lease_seconds=30, now=now)
        assert await orchestration.claim_workflow(workflow["id"], owner_id="worker-b", lease_seconds=30, now=now) is None
        reclaimed = await orchestration.claim_workflow(workflow["id"], owner_id="worker-b", lease_seconds=30, now=now + timedelta(seconds=31))
        assert reclaimed is not None and reclaimed["owner_id"] == "worker-b"
        assert not await orchestration.renew_workflow_lease(workflow["id"], owner_id="worker-a", lease_seconds=30)
        assert await orchestration.renew_workflow_lease(workflow["id"], owner_id="worker-b", lease_seconds=30)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_stage_items_store_typed_receipts_and_isolate_failures(tmp_path) -> None:
    engine, investigations, orchestration = await _repositories(tmp_path)
    try:
        investigation = await investigations.create(
            InvestigationCreate(
                title="Acme vs Beta",
                brief="Compare Acme and Beta for a product decision.",
                scope=ResearchScope(competitors=["Acme", "Beta"]),
            ),
            user_id="user-1",
        )
        workflow = await orchestration.ensure_workflow(investigation["id"], user_id="user-1", idempotency_key=f"{investigation['id']}:v1")
        await orchestration.claim_workflow(workflow["id"], owner_id="worker-a", lease_seconds=30)
        tasks = [
            StageTask(
                task_id=f"task-000{i}",
                investigation_id=investigation["id"],
                workflow_run_id=workflow["id"],
                stage=StageName.COLLECTING,
                item_key=f"competitor-{i}:pricing",
                role="competitor-collector",
                idempotency_key=f"{workflow['id']}:collect:{i}",
                input={"competitor": f"Competitor {i}", "dimension": "pricing"},
            )
            for i in (1, 2)
        ]
        attempt = await orchestration.start_stage(workflow["id"], owner_id="worker-a", stage=StageName.COLLECTING, tasks=tasks)
        resumed = await orchestration.start_stage(workflow["id"], owner_id="worker-a", stage=StageName.COLLECTING, tasks=tasks)
        assert resumed["id"] == attempt["id"]
        assert resumed["resumed"] is True
        now = datetime.now(UTC)
        success = AgentReceipt(
            task_id=tasks[0].task_id,
            investigation_id=investigation["id"],
            workflow_run_id=workflow["id"],
            stage=StageName.COLLECTING,
            item_key=tasks[0].item_key,
            role=tasks[0].role,
            status=ReceiptStatus.SUCCEEDED,
            attempt=1,
            submission_kind=SubmissionKind.EVIDENCE,
            started_at=now,
            completed_at=now,
        )
        await orchestration.update_stage_item(attempt["id"], item_key=tasks[0].item_key, status="succeeded", attempt=1, receipt=success)
        await orchestration.update_stage_item(attempt["id"], item_key=tasks[1].item_key, status="failed", attempt=1, error="provider timeout")
        items = await orchestration.list_stage_items(investigation["id"], user_id="user-1")
        assert items is not None
        assert {item["status"] for item in items} == {"succeeded", "failed"}
        assert next(item for item in items if item["status"] == "succeeded")["receipt"]["protocol_version"] == "ci-agent-v1"
        assert {item["task_id"] for item in items} == {task.task_id for task in tasks}
        assert await orchestration.list_stage_items(investigation["id"], user_id="other") is None

        with pytest.raises(WorkflowLeaseLost):
            await orchestration.start_stage(workflow["id"], owner_id="worker-b", stage=StageName.ANALYZING, tasks=[])
    finally:
        await engine.dispose()
