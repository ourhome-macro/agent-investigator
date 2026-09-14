from __future__ import annotations

import asyncio
from dataclasses import asdict, replace
from datetime import UTC, datetime
from typing import Any

from app.investigations.orchestration_repository import OrchestrationRepository
from app.investigations.protocols import (
    AgentReceipt,
    DomainSubmission,
    ReceiptStatus,
    StageName,
    StageTask,
    parse_domain_submission,
    render_stage_prompt,
)
from deerflow.subagents.batch_runtime import BatchItemInput, BatchSubmitRequest
from deerflow.subagents.registry import get_subagent_config


class DurableBatchUnavailable(RuntimeError):
    pass


class DurableStageBatchAdapter:
    def __init__(self, *, service: Any, batch_repository: Any, orchestration: OrchestrationRepository) -> None:
        self._service = service
        self._batch_repository = batch_repository
        self._orchestration = orchestration

    async def submit(
        self,
        *,
        stage_attempt_id: str,
        tasks: list[StageTask],
        user_id: str,
        model_name: str,
        instruction: str,
        max_running_items: int = 5,
    ) -> dict[str, Any]:
        if not tasks:
            raise ValueError("Durable stage batch requires at least one item")
        config = get_subagent_config("general-purpose")
        if config is None:
            raise DurableBatchUnavailable("general-purpose subagent is not registered")
        config = replace(
            config,
            model=model_name,
            tools=["web_search", "web_fetch"] if tasks[0].stage in {StageName.COLLECTING, StageName.REWORKING} else [],
            disallowed_tools=["task", "batch_task"],
        )
        items: list[BatchItemInput] = [
            {
                "key": task.item_key,
                "prompt": render_stage_prompt(task, instruction),
                "acceptance_criteria": task.acceptance_criteria or None,
            }
            for task in tasks
        ]
        first = tasks[0]
        batch = await self._service.submit(
            BatchSubmitRequest(
                user_id=user_id,
                thread_id=first.investigation_id,
                run_id=None,
                tool_call_id=f"ci-{stage_attempt_id}",
                submission_key=f"{first.workflow_run_id}:{first.stage.value}:{stage_attempt_id}",
                title=f"Competitive Research · {first.stage.value}",
                subagent_type="general-purpose",
                items=items,
                max_live_items=min(len(items), max(1, max_running_items * 2)),
                max_running_items=min(len(items), max_running_items),
                execution_spec={
                    "subagent_config": asdict(config),
                    "parent_model": model_name,
                    "tool_groups": None,
                    "user_role": "user",
                    "is_internal": True,
                    "authz_attributes": {},
                },
            )
        )
        batch_items = await self._batch_repository.list_items(
            batch["id"],
            user_id=user_id,
            offset=0,
            limit=max(100, len(items)),
            include_prompt=False,
            include_result=False,
        )
        item_ids = {item["item_key"]: item["id"] for item in batch_items or []}
        await self._orchestration.bind_durable_batch(stage_attempt_id, batch_id=batch["id"], item_ids_by_key=item_ids)
        return batch

    async def wait(
        self,
        *,
        batch_id: str,
        stage_attempt_id: str,
        tasks: list[StageTask],
        user_id: str,
        timeout_seconds: float = 1800,
    ) -> tuple[list[DomainSubmission], list[AgentReceipt]]:
        task_by_key = {task.item_key: task for task in tasks}
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        while True:
            batch = await self._service.get_batch(batch_id=batch_id, user_id=user_id)
            if batch is None:
                raise DurableBatchUnavailable("Durable batch disappeared")
            if batch["status"] in {"completed", "failed", "cancelled"}:
                break
            if loop.time() >= deadline:
                await self._service.cancel_batch(batch_id=batch_id, user_id=user_id)
                raise TimeoutError(f"Durable batch {batch_id} exceeded stage timeout")
            await asyncio.sleep(1)

        rows = await self._batch_repository.list_items(
            batch_id,
            user_id=user_id,
            offset=0,
            limit=max(100, len(tasks)),
            include_prompt=False,
            include_result=True,
        )
        submissions: list[DomainSubmission] = []
        receipts: list[AgentReceipt] = []
        for row in rows or []:
            task = task_by_key.get(row["item_key"])
            if task is None:
                continue
            now = datetime.now(UTC)
            started_at = row.get("started_at") or now
            completed_at = row.get("completed_at") or now
            status = ReceiptStatus.FAILED
            submission = None
            error = row.get("error")
            if row["status"] == "succeeded":
                try:
                    submission = parse_domain_submission(row.get("result") or "")
                    submission.require_matches(task)
                    status = ReceiptStatus.SUCCEEDED
                    submissions.append(submission)
                except Exception as exc:
                    status = ReceiptStatus.REJECTED
                    error = f"Invalid DomainSubmission: {exc}"
            receipt = AgentReceipt(
                task_id=task.task_id,
                investigation_id=task.investigation_id,
                workflow_run_id=task.workflow_run_id,
                stage=task.stage,
                item_key=task.item_key,
                role=task.role,
                status=status,
                attempt=max(1, int(row.get("attempt") or 1)),
                submission_kind=submission.kind if submission else None,
                durable_batch_id=batch_id,
                durable_batch_item_id=row.get("id"),
                model_name=row.get("model_name"),
                token_usage=row.get("token_usage"),
                error=error,
                started_at=started_at,
                completed_at=completed_at,
            )
            receipts.append(receipt)
            await self._orchestration.update_stage_item(
                stage_attempt_id,
                item_key=task.item_key,
                status=receipt.status.value,
                attempt=receipt.attempt,
                submission=submission,
                receipt=receipt,
                error=receipt.error,
            )
        return submissions, receipts
