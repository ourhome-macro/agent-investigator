from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.investigations.contracts import InvestigationCreate, ResearchScope
from app.investigations.persistence import upgrade_investigation_schema
from app.investigations.repository import InvestigationBudgetExceeded, InvestigationRepository


@pytest.mark.asyncio
async def test_budget_ledger_is_idempotent_and_reserves_audit_capacity(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'budget.db'}")
    try:
        await upgrade_investigation_schema(engine)
        repository = InvestigationRepository(async_sessionmaker(engine, expire_on_commit=False))
        investigation = await repository.create(
            InvestigationCreate(
                title="Budget test",
                brief="Verify that token accounting is durable and idempotent.",
                scope=ResearchScope(competitors=["Acme", "Beta"]),
            ),
            user_id="user-1",
        )
        usage = {"input_tokens": 200_000, "output_tokens": 39_000, "total_tokens": 239_000}
        first = await repository.record_token_usage(
            investigation["id"],
            user_id="user-1",
            workflow_run_id=None,
            stage="collecting",
            task_id="task-1",
            run_id=None,
            durable_batch_id="batch-1",
            model_name="deepseek-v4-flash",
            token_usage=usage,
            idempotency_key="budget-entry-1",
        )
        replay = await repository.record_token_usage(
            investigation["id"],
            user_id="user-1",
            workflow_run_id=None,
            stage="collecting",
            task_id="task-1",
            run_id=None,
            durable_batch_id="batch-1",
            model_name="deepseek-v4-flash",
            token_usage=usage,
            idempotency_key="budget-entry-1",
        )
        assert first == replay == {"used": 239_000, "budget": 300_000}
        with pytest.raises(InvestigationBudgetExceeded):
            await repository.assert_execution_budget(investigation["id"], user_id="user-1", stage="analyzing", estimated_tokens=2_000)
        audit_budget = await repository.assert_execution_budget(investigation["id"], user_id="user-1", stage="auditing", estimated_tokens=2_000)
        assert audit_budget["usable"] == 300_000
    finally:
        await engine.dispose()
