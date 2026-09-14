from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.investigations.contracts import ClaimCreate, EvidenceCreate, InvestigationCreate, InvestigationStatus, InvestigationType, ResearchScope
from app.investigations.persistence import upgrade_investigation_schema
from app.investigations.providers import ResearchProviderRegistry, SearchHit, canonicalize_url
from app.investigations.repository import InvestigationRepository
from app.investigations.scoring import claim_is_supported, credibility_score, independent_source_count
from app.investigations.service import InvestigationWorkflowService
from app.investigations.state_machine import InvalidInvestigationTransition, require_transition


def test_scope_requires_two_to_five_unique_competitors() -> None:
    scope = ResearchScope(competitors=["Acme", "acme", "Beta"])
    assert scope.competitors == ["Acme", "Beta"]
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
        assert version == "ci_0001"

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
        assert created["status"] == InvestigationStatus.AWAITING_SCOPE_APPROVAL.value
        assert created["scope"]["competitors"] == ["Acme", "Beta"]
        assert await repository.get(created["id"], user_id="other-user") is None

        evidence_ids = []
        for index, domain in enumerate(("acme.com", "industry.example")):
            evidence = await repository.add_evidence(
                created["id"],
                EvidenceCreate(
                    source_url=f"https://{domain}/pricing?utm_source=test",
                    canonical_url=f"https://{domain}/pricing",
                    source_domain=domain,
                    title=f"Pricing source {index}",
                    retrieved_at=datetime.now(UTC),
                    excerpt="Acme publishes a documented enterprise pricing tier.",
                    content_hash=f"{index + 1:064x}",
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
        claim = await repository.add_claim(
            created["id"],
            ClaimCreate(dimension="pricing", text="Acme has an enterprise pricing tier.", evidence_ids=evidence_ids),
            user_id="user-1",
        )
        assert claim is not None
        assert claim["status"] == "supported"
        assert claim["independent_source_count"] == 2
    finally:
        await engine.dispose()


def test_canonical_url_removes_tracking_and_fragment() -> None:
    assert canonicalize_url("HTTPS://Example.COM/path?utm_source=x&id=1#part") == "https://example.com/path?id=1"


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

        async def fetch(self, hit: SearchHit):
            return hit.content

    async def scripted_llm(**kwargs):
        package = __import__("json").loads(kwargs["user_content"])
        refs = [item["id"] for item in package["evidence"][:2]]
        return __import__("json").dumps([{"dimension": "功能", "text": "Acme 提供企业级能力。", "material": True, "evidence_ids": refs}], ensure_ascii=False)

    monkeypatch.setattr("app.investigations.service.run_oneshot_llm", scripted_llm)
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'workflow.db'}")
    try:
        await upgrade_investigation_schema(engine)
        repository = InvestigationRepository(async_sessionmaker(engine, expire_on_commit=False))
        created = await repository.create(
            InvestigationCreate(title="Acme vs Beta", brief="Compare product capability and pricing for strategy.", scope=ResearchScope(competitors=["Acme", "Beta"], dimensions=["功能"])),
            user_id="user-1",
        )
        await repository.approve_scope(created["id"], user_id="user-1", idempotency_key="approve-0001")
        service = InvestigationWorkflowService(repository, Providers())
        await service._execute(created["id"], "user-1")

        completed = await repository.get(created["id"], user_id="user-1")
        claims = await repository.list_claims(created["id"], user_id="user-1")
        report = await repository.latest_report(created["id"], user_id="user-1")
        assert completed is not None and completed["status"] == "awaiting_publish_approval"
        assert claims is not None and claims[0]["status"] == "supported"
        assert report is not None and "Evidence Appendix" in report["rendered_markdown"]
        assert len(report["structured_data"]["sections"]) == 11
    finally:
        await engine.dispose()
