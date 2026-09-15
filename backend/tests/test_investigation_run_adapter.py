from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from app.investigations.protocols import DomainSubmission, StageName, StageTask, SubmissionKind
from app.investigations.run_adapter import DeerFlowRunStageAdapter


@dataclass
class FakeRun:
    status: str
    last_ai_message: str | None
    error: str | None = None
    model_name: str | None = "deepseek-v4-pro"
    total_input_tokens: int = 10
    total_output_tokens: int = 5
    total_tokens: int = 15


def _task() -> StageTask:
    return StageTask(
        task_id="audit-task-0001",
        investigation_id="investigation-0001",
        workflow_run_id="workflow-0001",
        stage=StageName.AUDITING,
        item_key="evidence-audit:round-0",
        role="evidence-auditor",
        idempotency_key="workflow-0001:audit:round-0",
        input={"claims": []},
    )


@pytest.mark.asyncio
async def test_run_adapter_launches_normal_run_and_accepts_correlated_submission() -> None:
    task = _task()
    submission = DomainSubmission(
        task_id=task.task_id,
        investigation_id=task.investigation_id,
        workflow_run_id=task.workflow_run_id,
        stage=task.stage,
        item_key=task.item_key,
        kind=SubmissionKind.AUDIT,
        payload={"binding_verdicts": [], "issues": []},
    )
    launch_calls = []
    bound = []

    async def launch(**kwargs):
        launch_calls.append(kwargs)
        return {"run_id": "run-1", "thread_id": kwargs["thread_id"]}

    async def get_run(run_id, **kwargs):
        return FakeRun(status="success", last_ai_message=submission.model_dump_json())

    adapter = DeerFlowRunStageAdapter(
        launch_run=launch,
        get_run=get_run,
        app=SimpleNamespace(),
        poll_interval_seconds=0,
    )
    result, receipt = await adapter.execute(
        task,
        user_id="user-1",
        instruction="Audit claims.",
        on_run_started=lambda run_id: _append(bound, run_id),
    )

    assert result == submission
    assert receipt.status.value == "succeeded"
    assert receipt.run_id == "run-1"
    assert receipt.token_usage == {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}
    assert bound == ["run-1"]
    assert launch_calls[0]["assistant_id"] == "lead_agent"
    assert launch_calls[0]["metadata"]["ci_task_id"] == task.task_id


@pytest.mark.asyncio
async def test_run_adapter_reuses_persisted_run_and_rejects_wrong_envelope() -> None:
    task = _task()
    wrong = DomainSubmission(
        task_id=task.task_id,
        investigation_id=task.investigation_id,
        workflow_run_id=task.workflow_run_id,
        stage=task.stage,
        item_key="wrong-item",
        kind=SubmissionKind.AUDIT,
        payload={"binding_verdicts": [], "issues": []},
    )

    async def launch(**kwargs):
        raise AssertionError("persisted run must not be launched again")

    async def get_run(run_id, **kwargs):
        return FakeRun(status="success", last_ai_message=wrong.model_dump_json())

    adapter = DeerFlowRunStageAdapter(
        launch_run=launch,
        get_run=get_run,
        app=SimpleNamespace(),
        poll_interval_seconds=0,
    )
    result, receipt = await adapter.execute(
        task,
        user_id="user-1",
        instruction="Audit claims.",
        existing_run_id="run-existing",
    )
    assert result is None
    assert receipt.status.value == "rejected"
    assert "item_key" in (receipt.error or "")


async def _append(values: list[str], value: str) -> None:
    values.append(value)
