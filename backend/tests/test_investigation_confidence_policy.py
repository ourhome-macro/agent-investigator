"""Source-aware confidence: accept narrow claims without upgrading testimony."""

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.investigations.confidence import assess_support, is_official_source, issue_is_blocking
from app.investigations.contracts import ClaimCreate, EvidenceCreate, InvestigationCreate, InvestigationStatus, ResearchScope
from app.investigations.evidence_validation import sha256_text
from app.investigations.persistence import upgrade_investigation_schema
from app.investigations.quality import claim_is_eligible, render_grounded_report
from app.investigations.repository import InvestigationRepository


def source(domain="acme.test", official=True):
    return {"domain": domain, "official": official}


def test_official_single_source_can_support_a_documented_product_fact():
    assert assess_support("fact", "Acme supports Android.", [source()]) == "official_documented"
    assert assess_support("pricing", "The monthly plan costs USD 10.", [source()]) == "official_documented"


def test_self_reported_performance_is_never_independent_verification():
    assert assess_support("fact", "Acme is twice as fast as Beta.", [source()]) == "vendor_stated"
    assert assess_support("vendor_statement", "Acme has the best performance.", [source(), source("press.test", False)]) == "vendor_stated"
    assert assess_support("fact", "Acme is faster than Beta.", [source(official=False)]) == "unverified"
    assert assess_support("fact", "Acme is twice as fast as Beta.", [source(), source("acme-docs.test")]) == "vendor_stated"


def test_community_reports_keep_attribution_even_with_multiple_sources():
    assert assess_support("user_report", "Playback freezes on Android.", [source("forum.test", False)]) == "user_reported"
    assert assess_support("user_report", "Playback freezes on Android.", [source("forum.test", False), source("other.test", False)]) == "user_reported"
    assert assess_support("fact", "Playback freezes for every user.", [source("forum.test", False)]) == "unverified"


def test_official_identity_requires_the_actual_host_or_approved_repository():
    assert is_official_source("https://docs.acme.test/platforms", ["acme.test"], [])
    assert not is_official_source("https://acme.test.evil.test/docs", ["acme.test"], [])
    assert not is_official_source("https://github.com/random/project", ["github.com"], [])
    assert is_official_source("https://github.com/acme/player/releases/tag/v1", [], ["https://github.com/acme/player"])
    assert not is_official_source("https://github.com/acme/player-fake/releases", [], ["https://github.com/acme/player"])
    assert not is_official_source("https://github.com/acme/player/issues/1", [], ["https://github.com/acme/player"])
    assert not is_official_source("https://acme.test/forum/thread", ["acme.test"], [])
    assert not is_official_source("https://community.acme.test/t/123", ["acme.test"], [])
    assert not is_official_source("https://acme.test/video/123", ["acme.test"], [])


def test_advisories_do_not_block_claims_but_semantic_errors_still_do():
    claim = {"id": "c1", "status": "supported", "claim_type": "fact", "support_basis": "official_documented"}
    assert claim_is_eligible(claim, [{"claim_id": None, "severity": "warning", "rule": "missing_optional_detail", "status": "open"}])
    assert claim_is_eligible(claim, [{"claim_id": None, "section_id": "opportunities", "severity": "error", "rule": "format", "status": "open"}])
    assert not claim_is_eligible(claim, [{"claim_id": "c1", "severity": "warning", "rule": "semantic-support", "status": "open"}])
    assert not claim_is_eligible(claim, [{"claim_id": None, "severity": "error", "rule": "invalid_scope", "status": "open"}])
    assert not issue_is_blocking({"status": "resolved", "severity": "error", "rule": "contradiction"})


def report_inputs():
    investigation = {"title": "Platforms", "status": "synthesizing", "scope": {"competitors": ["Acme", "Beta"], "dimensions": ["platforms", "pricing"], "market": "Global", "time_range": "2026"}}
    competitors = [{"id": "a", "name": "Acme"}, {"id": "b", "name": "Beta"}]
    claims = [
        {
            "id": f"c{index}",
            "competitor_id": item["id"],
            "dimension": "platforms",
            "claim_type": "fact",
            "status": "supported",
            "support_basis": "official_documented",
            "version": 1,
            "text": f"{item['name']} supports Android.",
            "evidence_ids": [],
        }
        for index, item in enumerate(competitors)
    ]
    return investigation, competitors, claims


def test_completed_research_with_optional_gaps_and_no_opportunity_can_publish():
    investigation, competitors, claims = report_inputs()
    data, markdown = render_grounded_report(investigation, [], claims, [], [], competitors)
    assert data["partial"] is False
    assert data["completion_status"] == "completed_with_gaps"
    assert data["coverage_status"] == "has_gaps"
    assert "官方资料列示" in markdown
    assert "未发现有充分依据的机会建议" in markdown


@pytest.mark.parametrize("cause", ["missing_competitor", "required_dimension", "interrupted"])
def test_core_gaps_and_interrupted_execution_still_remain_incomplete(cause):
    investigation, competitors, claims = report_inputs()
    if cause == "missing_competitor":
        claims = claims[:1]
    elif cause == "required_dimension":
        investigation["scope"]["required_dimensions"] = ["pricing"]
    else:
        investigation["status"] = "failed"
    data, _ = render_grounded_report(investigation, [], claims, [], [], competitors)
    assert data["partial"] is True
    assert data["completion_status"] == "incomplete"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind,text,url,expected",
    [
        ("fact", "Acme supports Android.", "https://acme.test/docs", "official_documented"),
        ("pricing", "Acme Standard costs USD 10 per month.", "https://acme.test/pricing", "official_documented"),
        ("fact", "Acme is twice as fast as Beta.", "https://acme.test/docs", "vendor_stated"),
        ("user_report", "Playback freezes on Android.", "https://forum.test/thread/1", "user_reported"),
        ("fact", "Playback freezes for every user.", "https://forum.test/thread/1", "unverified"),
        ("fact", "Acme supports Android.", "https://github.com/acme/player/releases/tag/v1", "official_documented"),
        ("fact", "Acme supports Android.", "https://github.com/acme/player/issues/1", "unverified"),
    ],
)
async def test_repository_applies_source_policy_after_quote_audit(tmp_path, kind, text, url, expected):
    from urllib.parse import urlsplit

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'source-policy.db'}")
    try:
        await upgrade_investigation_schema(engine)
        repo = InvestigationRepository(async_sessionmaker(engine, expire_on_commit=False))
        scope = ResearchScope(competitors=["Acme", "Beta"], dimensions=["功能"], official_domains={"Acme": ["acme.test"]}, official_repositories={"Acme": ["https://github.com/acme/player"]})
        inv = await repo.create(InvestigationCreate(title="Source policy", brief="Synthetic single-source policy regression", scope=scope), user_id="owner")
        await repo.complete_planning(inv["id"], scope, user_id="owner", run_id=None)
        await repo.approve_scope(inv["id"], user_id="owner", idempotency_key="approve-source")
        competitor = next(item for item in await repo.list_competitors(inv["id"], user_id="owner") if item["name"] == "Acme")
        evidence = await repo.add_evidence(
            inv["id"],
            EvidenceCreate(
                competitor_id=competitor["id"],
                source_url=url,
                canonical_url=url,
                source_domain=urlsplit(url).hostname,
                source_type="documentation",
                title="Source",
                retrieved_at=datetime.now(UTC),
                excerpt=text,
                snapshot_text=text,
                extraction_method="direct_html",
                content_hash=sha256_text(text),
                source_authority=20,
                freshness=8,
                extraction_quality=8,
                specificity=7,
                corroboration=0,
            ),
            user_id="owner",
        )
        for stage in (InvestigationStatus.NORMALIZING, InvestigationStatus.ANALYZING):
            await repo.transition(inv["id"], stage, user_id="owner", event_type="ci.stage.started")
        prices = (
            [{"evidence_id": evidence["id"], "plan_name": "Standard", "amount": "10", "currency": "USD", "billing_period": "month", "official": True, "verbatim_quote": text, "snapshot_sha256": sha256_text(text)}]
            if kind == "pricing"
            else []
        )
        claim = await repo.add_claim(
            inv["id"],
            ClaimCreate(
                competitor_id=competitor["id"],
                dimension="功能",
                text=text,
                claim_type=kind,
                material=True,
                evidence_bindings=[{"evidence_id": evidence["id"], "verbatim_quote": text, "snapshot_sha256": sha256_text(text)}],
                price_observations=prices,
            ),
            user_id="owner",
        )
        assert claim["status"] == "uncertain"
        await repo.transition(inv["id"], InvestigationStatus.AUDITING, user_id="owner", event_type="ci.stage.started")
        await repo.apply_audit_verdicts(inv["id"], [{"claim_id": claim["id"], "evidence_id": evidence["id"], "relation": "supports", "verdict": "entails"}], user_id="owner", auditor="auditor")
        checked = (await repo.list_claims(inv["id"], user_id="owner"))[0]
        assert checked["support_basis"] == expected
        assert checked["publication_eligible"] is (expected != "unverified")
        assert checked["independent_source_count"] == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("required", [[], ["pricing"]])
async def test_publish_gate_uses_source_aware_completeness(tmp_path, required):
    from app.investigations.persistence.models import ClaimRow, InvestigationRow
    from app.investigations.repository import InvestigationConflict

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'publish-policy.db'}")
    try:
        await upgrade_investigation_schema(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        repo = InvestigationRepository(factory)
        scope = ResearchScope(competitors=["Acme", "Beta"], dimensions=["platforms", "pricing"], required_dimensions=required)
        inv = await repo.create(InvestigationCreate(title="Publish policy", brief="Synthetic publication gate state fixture", scope=scope), user_id="owner")
        competitors = await repo.list_competitors(inv["id"], user_id="owner")
        async with factory() as session, session.begin():
            (await session.get(InvestigationRow, inv["id"])).status = "auditing"
            for index, competitor in enumerate(competitors):
                session.add(
                    ClaimRow(
                        id=f"documented-{index}",
                        investigation_id=inv["id"],
                        competitor_id=competitor["id"],
                        dimension="platforms",
                        text=f"{competitor['name']} supports Android.",
                        normalized_text=f"platform-{index}",
                        claim_type="fact",
                        status="supported",
                        support_basis="official_documented",
                        independent_source_count=1,
                    )
                )
        await repo.replace_audit_issues(inv["id"], [{"rule": "missing_optional_detail", "reason": "Pricing not public", "severity": "warning", "required_action": "note"}], user_id="owner", raised_by="auditor")
        async with factory() as session, session.begin():
            (await session.get(InvestigationRow, inv["id"])).status = "synthesizing"
        claims = await repo.list_claims(inv["id"], user_id="owner")
        assert all(claim["publication_eligible"] for claim in claims)
        data, markdown = render_grounded_report(await repo.get(inv["id"], user_id="owner"), [], claims, [], await repo.list_audit_issues(inv["id"], user_id="owner"), competitors)
        # Publication must recheck the policy instead of trusting the partial flag.
        data["partial"] = False
        report = await repo.create_report(inv["id"], user_id="owner", structured_data=data, rendered_markdown=markdown)
        if required:
            with pytest.raises(InvestigationConflict, match="Core research"):
                await repo.approve_report(inv["id"], report["version"], user_id="owner", idempotency_key="publish-source-policy")
        else:
            published = await repo.approve_report(inv["id"], report["version"], user_id="owner", idempotency_key="publish-source-policy")
            assert published["status"] == "published"
            assert published["structured_data"]["completion_status"] == "completed_with_gaps"
    finally:
        await engine.dispose()
