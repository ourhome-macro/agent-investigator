"""Synthetic regressions derived from the player investigation failure modes."""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.investigations.contracts import InvestigationCreate, ResearchScope
from app.investigations.partial_report import build_partial_report
from app.investigations.persistence import upgrade_investigation_schema
from app.investigations.persistence.models import ClaimRow, InvestigationRow
from app.investigations.quality import admit_candidate, claim_is_eligible, coverage_cells, render_grounded_report
from app.investigations.repository import InvestigationBudgetExceeded, InvestigationConflict, InvestigationRepository


def test_admission_rejects_unrelated_and_unoffered_sources():
    offered = ["https://example.org/player"]
    assert not admit_candidate(url=offered[0], offered_urls=offered, competitor="PiliPala", dimension="离线", title="Airport news", excerpt="International airport traffic has increased.", official_domains=[]).accepted
    assert not admit_candidate(url="https://invented.org/", offered_urls=offered, competitor="PiliPala", dimension="离线", title="PiliPala", excerpt="PiliPala supports offline playback.", official_domains=[]).accepted
    assert admit_candidate(url=offered[0], offered_urls=offered, competitor="PiliPala", dimension="离线", title="PiliPala", excerpt="PiliPala supports offline playback.", official_domains=[]).accepted


def test_admission_is_not_specific_to_the_player_case():
    url = "https://docs.acme.test/cache"
    assert admit_candidate(url=url, offered_urls=[url], competitor="Acme Cache", dimension="persistence", title="Persistence", excerpt="Snapshots provide persistence for the cache.", official_domains=["docs.acme.test"]).accepted
    assert not admit_candidate(url=url, offered_urls=[url], competitor="Other Cache", dimension="persistence", title="Persistence", excerpt="Snapshots provide persistence for the cache.", official_domains=[]).accepted


def test_blocking_issue_and_retired_claim_never_qualify():
    claim = {"id": "c1", "status": "supported", "claim_type": "fact"}
    assert claim_is_eligible(claim, [])
    assert not claim_is_eligible(claim, [{"claim_id": "c1", "status": "open", "severity": "error"}])
    assert not claim_is_eligible({**claim, "status": "superseded"}, [])


def test_coverage_includes_competitors_without_any_claim():
    competitors = [{"id": "a", "name": "Acme"}, {"id": "b", "name": "Beta"}]
    claims = [{"id": "c1", "competitor_id": "a", "dimension": "离线", "status": "supported", "text": "Acme supports offline playback."}]
    cells = coverage_cells(competitors, ["离线", "投屏"], claims, [])
    assert len(cells) == 4
    assert sum(cell["status"] == "covered" for cell in cells) == 1
    assert all(cell["status"] == "missing" for cell in cells if cell["competitor_id"] == "b")


def test_report_rejects_generated_facts_and_routes_verified_claims():
    investigation = {"title": "Players", "scope": {"competitors": ["Acme", "Beta"], "dimensions": ["离线"], "market": "China", "time_range": "2026"}}
    claims = [{"id": "c1", "competitor_id": "a", "dimension": "离线", "status": "supported", "claim_type": "fact", "version": 1, "text": "Acme supports offline playback.", "evidence_ids": ["e1"]}]
    evidence = [{"id": "e1", "source_url": "https://acme.test/docs", "title": "Docs"}]
    with pytest.raises(ValueError, match="free-form"):
        render_grounded_report(investigation, [{"type": "feature_matrix", "markdown": "Acme has 900 million users.", "claim_ids": ["c1"]}], claims, evidence, [], [{"id": "a", "name": "Acme"}, {"id": "b", "name": "Beta"}])
    data, markdown = render_grounded_report(investigation, [{"type": "competitor_profiles", "claim_ids": ["c1"]}], claims, evidence, [], [{"id": "a", "name": "Acme"}, {"id": "b", "name": "Beta"}])
    assert "| Acme |" in markdown and "| Beta |" in markdown
    assert data["claim_versions"] == {"c1": 1}
    assert "900 million" not in markdown
    assert data["partial"] is True  # Beta remains unresearched.


def test_partial_report_does_not_dump_claim_into_every_section():
    investigation = {"title": "Players", "scope": {"market": "China", "time_range": "2026", "competitors": ["Acme", "Beta"]}}
    claim = {"id": "c1", "status": "supported", "dimension": "离线", "text": "Acme supports offline playback.", "evidence_ids": []}
    data, markdown = build_partial_report(investigation, [claim], [], [{"claim_id": "c1", "rule": "source", "reason": "Unverified source", "status": "open", "severity": "error"}])
    assert data["partial"]
    assert markdown.count(claim["text"]) <= 1
    assert "[supported]" not in markdown


@pytest.mark.asyncio
async def test_budget_reservation_is_atomic_idempotent_and_owner_scoped(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'control.db'}")
    try:
        await upgrade_investigation_schema(engine)
        repo = InvestigationRepository(async_sessionmaker(engine, expire_on_commit=False))
        row = await repo.create(InvestigationCreate(title="Budget", brief="Synthetic budget control regression", scope=ResearchScope(competitors=["Acme", "Beta"])), user_id="owner")
        args = dict(user_id="owner", stage="collecting", reservation_key="stage-one", tokens=180_000)
        await repo.reserve_budget(row["id"], **args)
        await repo.reserve_budget(row["id"], **args)
        with pytest.raises(InvestigationBudgetExceeded):
            await repo.reserve_budget(row["id"], user_id="owner", stage="collecting", reservation_key="stage-two", tokens=80_000)
        with pytest.raises(LookupError):
            await repo.reserve_budget(row["id"], **{**args, "user_id": "stranger"})
        await repo.settle_budget(row["id"], user_id="owner", reservation_key="stage-one", actual_tokens=20_000)
        await repo.settle_budget(row["id"], user_id="owner", reservation_key="stage-one", actual_tokens=20_000)
        await repo.reserve_budget(row["id"], user_id="owner", stage="collecting", reservation_key="stage-two", tokens=80_000)
        updated = await repo.get(row["id"], user_id="owner")
        assert updated["token_used"] == 20_000
        assert updated["token_reserved"] == 80_000
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_missing_issue_in_new_audit_is_not_resolution(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'issues.db'}")
    try:
        await upgrade_investigation_schema(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        repo = InvestigationRepository(factory)
        row = await repo.create(InvestigationCreate(title="Audit", brief="Synthetic audit issue retention regression", scope=ResearchScope(competitors=["Acme", "Beta"])), user_id="owner")
        async with factory() as session, session.begin():
            investigation = await session.get(InvestigationRow, row["id"])
            investigation.status = "auditing"
        first = await repo.replace_audit_issues(row["id"], [{"rule": "missing_dimension", "reason": "No evidence", "required_action": "recollect"}], user_id="owner", raised_by="auditor")
        next_audit = await repo.replace_audit_issues(row["id"], [], user_id="owner", raised_by="auditor")
        assert next_audit[0]["id"] == first[0]["id"]
        assert next_audit[0]["status"] == "open"
    finally:
        await engine.dispose()


def test_shared_host_is_not_proof_of_official_product_identity():
    url = "https://github.com/unrelated/repo"
    assert not admit_candidate(url=url, offered_urls=[url], competitor="PiliPala", dimension="功能", title="Other project", excerpt="Supports unrelated AI workflows.", official_domains=["github.com"]).accepted


@pytest.mark.asyncio
async def test_concurrent_reservations_cannot_overcommit(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'concurrent.db'}")
    try:
        await upgrade_investigation_schema(engine)
        repo = InvestigationRepository(async_sessionmaker(engine, expire_on_commit=False))
        inv = await repo.create(InvestigationCreate(title="Budget", brief="Synthetic concurrent reservation case", scope=ResearchScope(competitors=["Acme", "Beta"])), user_id="owner")
        outcomes = await asyncio.gather(*(repo.reserve_budget(inv["id"], user_id="owner", stage="collecting", reservation_key=f"r-{i}", tokens=150_000) for i in range(2)), return_exceptions=True)
        assert sum(outcome is None for outcome in outcomes) == 1
        assert sum(isinstance(outcome, InvestigationBudgetExceeded) for outcome in outcomes) == 1
        assert (await repo.get(inv["id"], user_id="owner"))["token_reserved"] == 150_000
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_retirement_and_publication_respect_audit_state(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'publication.db'}")
    try:
        await upgrade_investigation_schema(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        repo = InvestigationRepository(factory)
        inv = await repo.create(InvestigationCreate(title="Audit", brief="Synthetic publication and revision test", scope=ResearchScope(competitors=["Acme", "Beta"])), user_id="owner")
        async with factory() as session, session.begin():
            row = await session.get(InvestigationRow, inv["id"])
            row.status = "auditing"
            session.add(
                ClaimRow(
                    id="claim-original", investigation_id=inv["id"], dimension="功能", text="Unsupported extra capability", normalized_text="unsupported extra capability", material=True, claim_type="fact", status="supported", version=1
                )
            )
        issues = await repo.replace_audit_issues(inv["id"], [{"claim_id": "claim-original", "rule": "semantic_support", "reason": "Unsupported extra predicate", "required_action": "reject"}], user_id="owner", raised_by="auditor")
        async with factory() as session, session.begin():
            (await session.get(InvestigationRow, inv["id"])).status = "synthesizing"
        report = await repo.create_report(
            inv["id"], user_id="owner", structured_data={"schema_version": "competitive-report-v2", "partial": True, "claim_versions": {"claim-original": 1}, "sections": []}, rendered_markdown="Synthetic report"
        )
        with pytest.raises(InvestigationConflict, match="eligibility"):
            await repo.approve_report(inv["id"], report["version"], user_id="owner", idempotency_key="approve-1")
        async with factory() as session, session.begin():
            (await session.get(InvestigationRow, inv["id"])).status = "reworking"
        await repo.retire_claim(inv["id"], "claim-original", user_id="owner", expected_version=1, action="reject", replacement_ids=[])
        async with factory() as session, session.begin():
            (await session.get(InvestigationRow, inv["id"])).status = "auditing"
        await repo.apply_audit_verdicts(inv["id"], [], user_id="owner", auditor="auditor")
        assert (await repo.list_claims(inv["id"], user_id="owner"))[0]["status"] == "rejected"
        await repo.resolve_audit_issues(inv["id"], [{"issue_id": issues[0]["id"], "claim_version": 99, "reason": "Wrong version"}], user_id="owner")
        assert len(await repo.list_audit_issues(inv["id"], user_id="owner", status="open")) == 1
        await repo.resolve_audit_issues(inv["id"], [{"issue_id": issues[0]["id"], "claim_version": 1, "reason": "Rejected claim has been removed from eligible facts"}], user_id="owner")
        assert await repo.list_audit_issues(inv["id"], user_id="owner", status="open") == []
    finally:
        await engine.dispose()


def test_gateway_drops_external_budget_and_tool_scope_overrides():
    from app.gateway.services import merge_run_context_overrides, strip_internal_context_keys

    payload = {"token_budget_max_tokens": 999999999, "bounded_tool_names": ["bash"]}
    config = {"context": dict(payload), "configurable": dict(payload)}
    strip_internal_context_keys(config)
    merge_run_context_overrides(config, payload, internal=False)
    assert "token_budget_max_tokens" not in config["context"]
    assert "bounded_tool_names" not in config["configurable"]
    merge_run_context_overrides(config, payload, internal=True)
    assert config["configurable"]["token_budget_max_tokens"] == 999999999


@pytest.mark.asyncio
async def test_audit_chunks_have_independent_retry_counters(tmp_path):
    from app.investigations.orchestration_repository import OrchestrationRepository
    from app.investigations.protocols import StageName, StageTask

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'chunk-retries.db'}")
    try:
        await upgrade_investigation_schema(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        repo = InvestigationRepository(factory)
        orchestration = OrchestrationRepository(factory)
        inv = await repo.create(InvestigationCreate(title="Chunks", brief="Synthetic chunk retry isolation case", scope=ResearchScope(competitors=["Acme", "Beta"])), user_id="owner")
        workflow = await orchestration.ensure_workflow(inv["id"], user_id="owner", idempotency_key="chunk-workflow")
        await orchestration.claim_workflow(workflow["id"], owner_id="worker", lease_seconds=120)
        for index in range(4):
            task = StageTask(task_id=f"audit-task-{index}", investigation_id=inv["id"], workflow_run_id=workflow["id"], stage=StageName.AUDITING, item_key=f"chunk-{index}", role="auditor", idempotency_key=f"audit-key-{index}", input={})
            attempt = await orchestration.start_stage(workflow["id"], owner_id="worker", stage=StageName.AUDITING, tasks=[task])
            assert attempt["task_attempt"] == 1
            await orchestration.finish_stage(attempt["id"], succeeded=index != 3)
        retry = await orchestration.start_stage(workflow["id"], owner_id="worker", stage=StageName.AUDITING, tasks=[task])
        assert retry["task_attempt"] == 2
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_upgrade_existing_ci_0009_preserves_investigation(tmp_path):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'upgrade.db'}")
    try:
        await upgrade_investigation_schema(engine)
        repo = InvestigationRepository(async_sessionmaker(engine, expire_on_commit=False))
        row = await repo.create(InvestigationCreate(title="Existing", brief="Synthetic existing database migration test", scope=ResearchScope(competitors=["Acme", "Beta"])), user_id="owner")

        def old_schema(connection):
            config = Config()
            config.set_main_option("script_location", str(Path(__file__).parents[1] / "app/investigations/persistence/migrations"))
            config.attributes["connection"] = connection
            command.downgrade(config, "ci_0009")

        async with engine.begin() as connection:
            await connection.run_sync(old_schema)
        await upgrade_investigation_schema(engine)
        restored = await repo.get(row["id"], user_id="owner")
        assert restored["title"] == "Existing"
        assert restored["token_reserved"] == 0
    finally:
        await engine.dispose()
