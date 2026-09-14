from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from app.investigations.protocols import (
    AgentReceipt,
    DomainSubmission,
    ReceiptStatus,
    StageTask,
    parse_domain_submission,
    render_stage_prompt,
)

LaunchRun = Callable[..., Awaitable[dict[str, Any]]]
GetRun = Callable[..., Awaitable[Any | None]]


class DeerFlowRunStageAdapter:
    """Execute one CI stage task through the ordinary DeerFlow run lifecycle."""

    TERMINAL = {"success", "error", "timeout", "interrupted"}

    def __init__(
        self,
        *,
        launch_run: LaunchRun,
        get_run: GetRun,
        app: Any,
        poll_interval_seconds: float = 1.0,
        timeout_seconds: float = 900.0,
    ) -> None:
        self._launch_run = launch_run
        self._get_run = get_run
        self._app = app
        self._poll_interval_seconds = poll_interval_seconds
        self._timeout_seconds = timeout_seconds

    @classmethod
    def from_app(cls, app: Any) -> DeerFlowRunStageAdapter:
        from app.gateway.services import launch_scheduled_thread_run

        return cls(
            launch_run=launch_scheduled_thread_run,
            get_run=app.state.run_manager.get,
            app=app,
        )

    async def execute(
        self,
        task: StageTask,
        *,
        user_id: str,
        instruction: str,
        existing_run_id: str | None = None,
        on_run_started: Callable[[str], Awaitable[None]] | None = None,
    ) -> tuple[DomainSubmission | None, AgentReceipt]:
        started_at = datetime.now(UTC)
        if existing_run_id is None:
            launched = await self._launch_run(
                app=self._app,
                thread_id=f"ci-{task.investigation_id}",
                assistant_id="lead_agent",
                prompt=render_stage_prompt(task, instruction),
                owner_user_id=user_id,
                metadata={
                    "competitive_investigation_id": task.investigation_id,
                    "ci_workflow_run_id": task.workflow_run_id,
                    "ci_stage": task.stage.value,
                    "ci_task_id": task.task_id,
                },
            )
            run_id = str(launched["run_id"])
            if on_run_started is not None:
                await on_run_started(run_id)
        else:
            run_id = existing_run_id
        try:
            record = await self._wait_for_run(run_id, user_id=user_id)
            status = self._status_value(record)
            if status != "success":
                return None, self._receipt(
                    task,
                    started_at=started_at,
                    run_id=run_id,
                    record=record,
                    status=ReceiptStatus.FAILED,
                    error=getattr(record, "error", None) or f"DeerFlow run ended with status={status}",
                )
            raw = getattr(record, "last_ai_message", None)
            if not isinstance(raw, str) or not raw.strip():
                return None, self._receipt(
                    task,
                    started_at=started_at,
                    run_id=run_id,
                    record=record,
                    status=ReceiptStatus.REJECTED,
                    error="DeerFlow run produced no final AI message",
                )
            try:
                submission = parse_domain_submission(raw)
                submission.require_matches(task)
            except (ValueError, TypeError) as exc:
                return None, self._receipt(
                    task,
                    started_at=started_at,
                    run_id=run_id,
                    record=record,
                    status=ReceiptStatus.REJECTED,
                    error=f"Invalid DomainSubmission: {exc}",
                )
            return submission, self._receipt(
                task,
                started_at=started_at,
                run_id=run_id,
                record=record,
                status=ReceiptStatus.SUCCEEDED,
                submission=submission,
            )
        except TimeoutError as exc:
            return None, self._receipt(
                task,
                started_at=started_at,
                run_id=run_id,
                record=None,
                status=ReceiptStatus.FAILED,
                error=str(exc),
            )

    async def _wait_for_run(self, run_id: str, *, user_id: str) -> Any:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + self._timeout_seconds
        while loop.time() < deadline:
            record = await self._get_run(run_id, user_id=user_id)
            if record is None:
                raise RuntimeError(f"DeerFlow run disappeared: {run_id}")
            if self._status_value(record) in self.TERMINAL:
                return record
            await asyncio.sleep(self._poll_interval_seconds)
        raise TimeoutError(f"DeerFlow run exceeded {self._timeout_seconds:g} seconds")

    @staticmethod
    def _status_value(record: Any) -> str:
        status = getattr(record, "status", "")
        return str(getattr(status, "value", status))

    @staticmethod
    def _receipt(
        task: StageTask,
        *,
        started_at: datetime,
        run_id: str,
        record: Any | None,
        status: ReceiptStatus,
        submission: DomainSubmission | None = None,
        error: str | None = None,
    ) -> AgentReceipt:
        token_usage = None
        if record is not None:
            token_usage = {
                "input_tokens": int(getattr(record, "total_input_tokens", 0)),
                "output_tokens": int(getattr(record, "total_output_tokens", 0)),
                "total_tokens": int(getattr(record, "total_tokens", 0)),
            }
        return AgentReceipt(
            task_id=task.task_id,
            investigation_id=task.investigation_id,
            workflow_run_id=task.workflow_run_id,
            stage=task.stage,
            item_key=task.item_key,
            role=task.role,
            status=status,
            attempt=1,
            submission_kind=submission.kind if submission else None,
            run_id=run_id,
            model_name=getattr(record, "model_name", None) if record is not None else None,
            token_usage=token_usage,
            warnings=submission.warnings if submission else [],
            error=error,
            started_at=started_at,
            completed_at=datetime.now(UTC),
        )
