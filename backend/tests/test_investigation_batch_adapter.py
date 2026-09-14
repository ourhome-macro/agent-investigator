from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.investigations.batch_adapter import DurableStageBatchAdapter
from app.investigations.protocols import DomainSubmission, StageName, StageTask, SubmissionKind


class FakeOrchestration:
    def __init__(self) -> None:
        self.bound = None
        self.updates = []

    async def bind_durable_batch(self, stage_attempt_id, *, batch_id, item_ids_by_key):
        self.bound = (stage_attempt_id, batch_id, item_ids_by_key)

    async def update_stage_item(self, stage_attempt_id, **kwargs):
        self.updates.append((stage_attempt_id, kwargs))


class FakeBatchService:
    def __init__(self) -> None:
        self.request = None

    async def submit(self, request):
        self.request = request
        return {"id": "batch-1", "status": "queued", "total_items": len(request.items)}

    async def get_batch(self, *, batch_id, user_id):
        return {"id": batch_id, "status": "completed", "counts": {"succeeded": 1}}


class FakeBatchRepository:
    def __init__(self, result: str) -> None:
        self.result = result

    async def list_items(self, batch_id, **kwargs):
        if kwargs["include_result"]:
            now = datetime.now(UTC)
            return [
                {
                    "id": "batch-item-1",
                    "item_key": "acme:pricing",
                    "status": "succeeded",
                    "attempt": 1,
                    "result": self.result,
                    "error": None,
                    "model_name": "deepseek-v4-flash",
                    "token_usage": {"total_tokens": 42},
                    "started_at": now,
                    "completed_at": now,
                }
            ]
        return [{"id": "batch-item-1", "item_key": "acme:pricing"}]


@pytest.mark.asyncio
async def test_adapter_submits_stable_batch_and_validates_correlated_result() -> None:
    task = StageTask(
        task_id="task-0001",
        investigation_id="investigation-0001",
        workflow_run_id="workflow-0001",
        stage=StageName.COLLECTING,
        item_key="acme:pricing",
        role="competitor-collector",
        idempotency_key="workflow-0001:collect:acme",
        input={"competitor": "Acme", "dimension": "pricing"},
        acceptance_criteria=["At least one evidence record"],
    )
    submission = DomainSubmission(
        task_id=task.task_id,
        investigation_id=task.investigation_id,
        workflow_run_id=task.workflow_run_id,
        stage=task.stage,
        item_key=task.item_key,
        kind=SubmissionKind.EVIDENCE,
        payload={"evidence": []},
    )
    orchestration = FakeOrchestration()
    service = FakeBatchService()
    adapter = DurableStageBatchAdapter(
        service=service,
        batch_repository=FakeBatchRepository(submission.model_dump_json()),
        orchestration=orchestration,
    )

    batch = await adapter.submit(
        stage_attempt_id="attempt-1",
        tasks=[task],
        user_id="user-1",
        model_name="deepseek-v4-flash",
        instruction="Collect evidence.",
    )
    submissions, receipts = await adapter.wait(
        batch_id=batch["id"],
        stage_attempt_id="attempt-1",
        tasks=[task],
        user_id="user-1",
    )

    assert service.request.submission_key == "workflow-0001:collecting:attempt-1"
    assert service.request.items[0]["key"] == task.item_key
    assert orchestration.bound == ("attempt-1", "batch-1", {"acme:pricing": "batch-item-1"})
    assert submissions == [submission]
    assert receipts[0].status.value == "succeeded"
    assert receipts[0].token_usage == {"total_tokens": 42}
    assert orchestration.updates[0][1]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_adapter_rejects_successful_batch_item_with_wrong_envelope() -> None:
    task = StageTask(
        task_id="task-0001",
        investigation_id="investigation-0001",
        workflow_run_id="workflow-0001",
        stage=StageName.COLLECTING,
        item_key="acme:pricing",
        role="competitor-collector",
        idempotency_key="workflow-0001:collect:acme",
        input={},
    )
    wrong = DomainSubmission(
        task_id=task.task_id,
        investigation_id=task.investigation_id,
        workflow_run_id=task.workflow_run_id,
        stage=task.stage,
        item_key="beta:pricing",
        kind=SubmissionKind.EVIDENCE,
        payload={"evidence": []},
    )
    orchestration = FakeOrchestration()
    adapter = DurableStageBatchAdapter(
        service=FakeBatchService(),
        batch_repository=FakeBatchRepository(wrong.model_dump_json()),
        orchestration=orchestration,
    )
    submissions, receipts = await adapter.wait(
        batch_id="batch-1",
        stage_attempt_id="attempt-1",
        tasks=[task],
        user_id="user-1",
    )
    assert submissions == []
    assert receipts[0].status.value == "rejected"
    assert "item_key" in (receipts[0].error or "")
