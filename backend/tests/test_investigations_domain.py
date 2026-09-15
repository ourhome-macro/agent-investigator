from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import event, inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.investigations.contracts import ClaimCreate, EvidenceCreate, InvestigationCreate, InvestigationStatus, InvestigationType, ResearchScope
from app.investigations.evidence_validation import sha256_text
from app.investigations.persistence import upgrade_investigation_schema
from app.investigations.protocols import DomainSubmission, StageName, StageTask, SubmissionKind, parse_domain_submission, render_stage_prompt
from app.investigations.providers import ResearchProviderRegistry, SearchHit, SourceDocument, SourceType, canonicalize_url
from app.investigations.repository import InvestigationRepository
from app.investigations.scoring import claim_is_supported, credibility_score, independent_source_count
from app.investigations.service import InvestigationWorkflowService
from app.investigations.state_machine import InvalidInvestigationTransition, require_transition


def test_scope_requires_two_to_five_unique_competitors() -> None:
    scope = ResearchScope(
        competitors=["Acme", "acme", "Beta"],
        official_domains={"Acme": ["https://www.acme.com/pricing", "acme.com/docs"]},
    )
    assert scope.competitors == ["Acme", "Beta"]
    assert scope.official_domains == {"Acme": ["acme.com"]}
    with pytest.raises(ValidationError):
        ResearchScope(competitors=["Only one"])


def test_state_machine_enforces_approval_and_publish_gates() -> None:
    require_transition(InvestigationStatus.PLANNING, InvestigationStatus.AWAITING_SCOPE_APPROVAL)
    require_transition(InvestigationStatus.AWAITING_SCOPE_APPROVAL, InvestigationStatus.COLLECTING)
    require_transition(InvestigationStatus.AWAITING_PUBLISH_APPROVAL, InvestigationStatus.PUBLISHED)
    with pytest.raises(InvalidInvestigationTransition):
        require_transition(InvestigationStatus.PLANNING, InvestigationStatus.COLLECTING)
    with pytest.raises(InvalidInvestigationTransition):
        require_transition(InvestigationStatus.AUDITING, InvestigationStatus.PUBLISHED)


def test_active_work_can_cancel_but_terminal_work_cannot() -> None:
    require_transition(InvestigationStatus.ANALYZING, InvestigationStatus.CANCELLING)
    require_transition(InvestigationStatus.CANCELLING, InvestigationStatus.CANCELLED)
    with pytest.raises(InvalidInvestigationTransition):
        require_transition(InvestigationStatus.PUBLISHED, InvestigationStatus.CANCELLING)


def test_credibility_score_is_deterministic_and_bounded() -> None:
    assert credibility_score(source_authority=25, freshness=15, extraction_quality=8, specificity=7, corroboration=20) == 75
    with pytest.raises(ValueError):
        credibility_score(source_authority=31, freshness=0, extraction_quality=0, specificity=0, corroboration=0)


def test_material_claim_needs_two_independent_domains() -> None:
    domains = ["www.example.com", "example.com", "official.example.org"]
    assert independent_source_count(domains) == 2
    assert claim_is_supported(material=True, supporting_domains=domains)
    assert not claim_is_supported(material=True, supporting_domains=["example.com"])
    assert claim_is_supported(material=False, supporting_domains=["example.com"])
    assert independent_source_count(["news.example.com", "www.example.com"]) == 1


@pytest.mark.asyncio
async def test_independent_migration_and_repository_round_trip(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'ci.db'}")
    try:
        await upgrade_investigation_schema(engine)

        async with engine.connect() as connection:
            tables = await connection.run_sync(lambda sync_connection: set(inspect(sync_connection).get_table_names()))
            version = (await connection.execute(text("SELECT version_num FROM alembic_version_ci"))).scalar_one()

        assert "ci_investigations" in tables
        assert "ci_claim_evidence" in tables
        assert "ci_stage_items" in tables
        assert version == "ci_0011"

        repository = InvestigationRepository(async_sessionmaker(engine, expire_on_commit=False))
        created = await repository.create(
            InvestigationCreate(
                investigation_type=InvestigationType.COMPETITIVE_RESEARCH,
                title="Acme competitor research",
                brief="Compare Acme and Beta for a product strategy decision.",
                scope=ResearchScope(competitors=["Acme", "Beta"]),
            ),
            user_id="user-1",
        )
        assert created["status"] == InvestigationStatus.PLANNING.value
        assert created["scope"]["competitors"] == ["Acme", "Beta"]
        assert await repository.get(created["id"], user_id="other-user") is None
        await repository.complete_planning(
            created["id"],
            ResearchScope(competitors=["Acme", "Beta"], official_domains={"Acme": ["acme.com"]}),
            user_id="user-1",
            run_id="planning-run-1",
        )
        await repository.approve_scope(created["id"], user_id="user-1", idempotency_key="approve-scope-1")
        competitors = await repository.list_competitors(created["id"], user_id="user-1")
        assert competitors is not None
        acme_id = next(item["id"] for item in competitors if item["name"] == "Acme")

        evidence_ids = []
        evidence_hashes = []
        quote = "Acme publishes a documented enterprise capability tier."
        for index, domain in enumerate(("acme.com", "industry.example")):
            snapshot = f"Source {index}. {quote} Additional context for verification."
            evidence = await repository.add_evidence(
                created["id"],
                EvidenceCreate(
                    source_url=f"https://{domain}/pricing?utm_source=test",
                    canonical_url=f"https://{domain}/pricing",
                    source_domain=domain,
                    title=f"Pricing source {index}",
                    retrieved_at=datetime.now(UTC),
                    excerpt=quote,
                    snapshot_text=snapshot,
                    content_hash=sha256_text(snapshot),
                    source_authority=25,
                    freshness=20,
                    extraction_quality=10,
                    specificity=10,
                    corroboration=20,
                ),
                user_id="user-1",
            )
            assert evidence is not None
            evidence_ids.append(evidence["id"])
            evidence_hashes.append(evidence["content_hash"])
        price_snapshot = "The Pro plan costs CNY 199 per month for each account."
        price_evidence = await repository.add_evidence(
            created["id"],
            EvidenceCreate(
                competitor_id=acme_id,
                source_url="https://acme.com/pricing",
                canonical_url="https://acme.com/pricing",
                source_domain="acme.com",
                source_type="pricing",
                title="Official pricing",
                retrieved_at=datetime.now(UTC),
                excerpt=price_snapshot,
                snapshot_text=price_snapshot,
                content_hash=sha256_text(price_snapshot),
                source_authority=30,
                freshness=20,
                extraction_quality=10,
                specificity=10,
                corroboration=0,
            ),
            user_id="user-1",
        )
        assert price_evidence is not None
        await repository.transition(
            created["id"],
            InvestigationStatus.NORMALIZING,
            user_id="user-1",
            event_type="ci.stage.completed",
        )
        await repository.transition(
            created["id"],
            InvestigationStatus.ANALYZING,
            user_id="user-1",
            event_type="ci.stage.started",
        )
        claim = await repository.add_claim(
            created["id"],
            ClaimCreate(
                dimension="功能",
                text="Acme publishes a documented enterprise capability tier.",
                evidence_bindings=[
                    {
                        "evidence_id": evidence_id,
                        "verbatim_quote": quote,
                        "snapshot_sha256": evidence_hashes[index],
                    }
                    for index, evidence_id in enumerate(evidence_ids)
                ],
            ),
            user_id="user-1",
        )
        assert claim is not None
        assert claim["status"] == "uncertain"
        assert claim["independent_source_count"] == 2

        pricing_claim = await repository.add_claim(
            created["id"],
            ClaimCreate(
                dimension="定价",
                text="Acme Pro 套餐价格为 CNY 199 每月。",
                claim_type="pricing",
                material=True,
                evidence_bindings=[
                    {
                        "evidence_id": price_evidence["id"],
                        "verbatim_quote": price_snapshot,
                        "snapshot_sha256": price_evidence["content_hash"],
                    }
                ],
                price_observations=[
                    {
                        "evidence_id": price_evidence["id"],
                        "plan_name": "Pro",
                        "amount": "199",
                        "currency": "CNY",
                        "billing_period": "month",
                        "official": True,
                        "verbatim_quote": price_snapshot,
                        "snapshot_sha256": price_evidence["content_hash"],
                    }
                ],
            ),
            user_id="user-1",
        )
        assert pricing_claim is not None
        prices = await repository.list_price_observations(created["id"], user_id="user-1")
        assert prices is not None and prices[0]["amount"] == "199" and prices[0]["currency"] == "CNY"

        uncertain = await repository.add_claim(
            created["id"],
            ClaimCreate(
                dimension="功能",
                text="Acme publishes a documented enterprise capability tier.",
                material=True,
                evidence_bindings=[
                    {
                        "evidence_id": evidence_ids[0],
                        "verbatim_quote": quote,
                        "snapshot_sha256": evidence_hashes[0],
                    }
                ],
            ),
            user_id="user-1",
            agent_name="pricing-analyst",
            idempotency_key="analysis-task-1:claim:0",
        )
        assert uncertain is not None and uncertain["status"] == "uncertain"
        replayed = await repository.add_claim(
            created["id"],
            ClaimCreate(
                dimension="功能",
                text="This changed replay payload must not create another Claim.",
                material=True,
                evidence_bindings=[
                    {
                        "evidence_id": evidence_ids[0],
                        "verbatim_quote": quote,
                        "snapshot_sha256": evidence_hashes[0],
                    }
                ],
            ),
            user_id="user-1",
            agent_name="pricing-analyst",
            idempotency_key="analysis-task-1:claim:0",
        )
        assert replayed is not None and replayed["id"] == uncertain["id"]
        await repository.transition(
            created["id"],
            InvestigationStatus.AUDITING,
            user_id="user-1",
            event_type="ci.stage.started",
        )
        await repository.apply_audit_verdicts(
            created["id"],
            [
                {
                    "claim_id": claim["id"],
                    "evidence_id": evidence_id,
                    "relation": "supports",
                    "verdict": "entails",
                }
                for evidence_id in evidence_ids
            ],
            user_id="user-1",
            auditor="test-auditor",
        )
        audited_claims = await repository.list_claims(created["id"], user_id="user-1")
        assert audited_claims is not None
        assert next(item for item in audited_claims if item["id"] == claim["id"])["status"] == "supported"
        issues = await repository.replace_audit_issues(
            created["id"],
            [
                {
                    "claim_id": uncertain["id"],
                    "severity": "warning",
                    "rule": "semantic_support",
                    "reason": "Excerpt is indirect.",
                    "required_action": "Collect an official pricing page.",
                }
            ],
            user_id="user-1",
            raised_by="evidence-auditor",
        )
        assert issues is not None and issues[0]["status"] == "open"
        assert len(await repository.list_audit_issues(created["id"], user_id="user-1", status="open") or []) == 1
        await repository.begin_audit_rework(created["id"], user_id="user-1", issue_ids=[issues[0]["id"]])
        supplemented = await repository.supplement_claim_evidence(created["id"], uncertain["id"], [evidence_ids[1]], user_id="user-1")
        assert supplemented is not None and supplemented["status"] == "uncertain"
    finally:
        await engine.dispose()


def test_canonical_url_removes_tracking_and_fragment() -> None:
    assert canonicalize_url("HTTPS://Example.COM/path?utm_source=x&id=1#part") == "https://example.com/path?id=1"


@pytest.mark.asyncio
async def test_create_flushes_investigation_before_fk_children(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'foreign-keys.db'}")

    @event.listens_for(engine.sync_engine, "connect")
    def enable_foreign_keys(connection, _record) -> None:
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    try:
        await upgrade_investigation_schema(engine)
        repository = InvestigationRepository(async_sessionmaker(engine, expire_on_commit=False))
        created = await repository.create(
            InvestigationCreate(
                title="Foreign key ordering",
                brief="Exercise the exact SQLite settings used by the running Gateway.",
                scope=ResearchScope(competitors=["Acme", "Beta"]),
            ),
            user_id="user-1",
        )
        assert created["scope"]["competitors"] == ["Acme", "Beta"]
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_failed_approved_execution_can_start_recovery_round(tmp_path) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'recovery.db'}")
    try:
        await upgrade_investigation_schema(engine)
        repository = InvestigationRepository(async_sessionmaker(engine, expire_on_commit=False))
        created = await repository.create(
            InvestigationCreate(
                title="Recovery",
                brief="Retry a failed execution without repeating the approved planning run.",
                scope=ResearchScope(competitors=["Acme", "Beta"]),
            ),
            user_id="user-1",
        )
        await repository.complete_planning(created["id"], ResearchScope.model_validate(created["scope"]), user_id="user-1", run_id="planning-run")
        await repository.approve_scope(created["id"], user_id="user-1", idempotency_key="approve-recovery")
        await repository.transition(
            created["id"],
            InvestigationStatus.FAILED,
            user_id="user-1",
            event_type="ci.failed",
        )
        retried = await repository.retry_failed(created["id"], user_id="user-1", idempotency_key="retry-recovery")
        assert retried is not None
        assert retried["status"] == "collecting"
        assert retried["rework_round"] == 0
        assert retried["failure_retry_count"] == 1
    finally:
        await engine.dispose()


def test_stage_protocol_requires_exact_task_correlation() -> None:
    task = StageTask(
        task_id="task-0001",
        investigation_id="investigation-0001",
        workflow_run_id="workflow-0001",
        stage=StageName.COLLECTING,
        item_key="acme:pricing",
        role="competitor-collector",
        idempotency_key="workflow-0001:acme:pricing",
        input={"competitor": "Acme", "dimension": "pricing"},
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
    submission.require_matches(task)
    parsed = parse_domain_submission(submission.model_dump_json())
    parsed.require_matches(task)
    assert "<stage-task>" in render_stage_prompt(task, "Collect evidence.")

    mismatched = submission.model_copy(update={"item_key": "other"})
    with pytest.raises(ValueError, match="item_key"):
        mismatched.require_matches(task)

    with pytest.raises(ValueError, match="valid string"):
        DomainSubmission(
            task_id="planning-task-1",
            investigation_id="investigation-0001",
            workflow_run_id="workflow-0001",
            stage=StageName.PLANNING,
            item_key="scope-draft",
            kind=SubmissionKind.SCOPE,
            payload={"scope": {"competitors": [{"name": "Acme"}, {"name": "Beta"}]}},
        )


@pytest.mark.asyncio
async def test_strict_production_requires_a_search_provider(monkeypatch) -> None:
    monkeypatch.setenv("DEER_FLOW_ENV", "production")
    monkeypatch.delenv("BOCHA_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    providers = ResearchProviderRegistry()
    with pytest.raises(RuntimeError, match="production providers missing"):
        providers.validate_startup()
    with pytest.raises(RuntimeError, match="requires BOCHA_API_KEY or TAVILY_API_KEY"):
        await providers.search("Acme competitors")


@pytest.mark.asyncio
async def test_workflow_reaches_report_review_with_auditable_claims(tmp_path, monkeypatch) -> None:
    class Providers:
        def status(self):
            return {"scripted": True}

        def validate_startup(self):
            return None

        async def search(self, query: str, *, max_results: int = 5):
            slug = str(abs(hash(query)))
            return [
                SearchHit(title="Official", url=f"https://official.example/{slug}", content=f"Official product capability and pricing documentation for research query {slug}.", provider="scripted"),
                SearchHit(title="Independent", url=f"https://independent.example/{slug}", content=f"Independent product review confirming the documented capability for query {slug}.", provider="scripted"),
            ]

        async def fetch_url(self, url: str, *, fallback_content: str, investigation_id: str):
            return SourceDocument(url=url, content=fallback_content, source_type=SourceType.WEB, extraction_method="scripted")

    async def scripted_llm(**kwargs):
        package = __import__("json").loads(kwargs["user_content"])
        sources = package["evidence"][:2]
        return __import__("json").dumps(
            [
                {
                    "dimension": "功能",
                    "text": "Acme 提供企业级能力。",
                    "material": True,
                    "evidence_bindings": [
                        {
                            "evidence_id": item["id"],
                            "verbatim_quote": item["excerpt"],
                            "snapshot_sha256": item["content_hash"],
                        }
                        for item in sources
                    ],
                }
            ],
            ensure_ascii=False,
        )

    monkeypatch.setattr("app.investigations.service.run_oneshot_llm", scripted_llm)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'workflow.db'}")
    try:
        await upgrade_investigation_schema(engine)
        repository = InvestigationRepository(async_sessionmaker(engine, expire_on_commit=False))
        created = await repository.create(
            InvestigationCreate(title="Acme vs Beta", brief="Compare product capability and pricing for strategy.", scope=ResearchScope(competitors=["Acme", "Beta"], dimensions=["功能"])),
            user_id="user-1",
        )
        await repository.complete_planning(
            created["id"],
            ResearchScope(competitors=["Acme", "Beta"], dimensions=["功能"]),
            user_id="user-1",
            run_id="planning-run-1",
        )
        await repository.approve_scope(created["id"], user_id="user-1", idempotency_key="approve-0001")
        service = InvestigationWorkflowService(repository, Providers())
        await service._execute(created["id"], "user-1")

        completed = await repository.get(created["id"], user_id="user-1")
        claims = await repository.list_claims(created["id"], user_id="user-1")
        report = await repository.latest_report(created["id"], user_id="user-1")
        assert completed is not None and completed["status"] == "awaiting_publish_approval"
        assert claims is not None and claims[0]["status"] == "uncertain"
        assert report is not None and "Evidence Appendix" in report["rendered_markdown"]
        assert len(report["structured_data"]["sections"]) == 11
    finally:
        await engine.dispose()
