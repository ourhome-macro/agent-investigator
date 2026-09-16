"""Decision views, snapshotted mode policy and version-bound follow-up research."""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.investigations.annotation_repository import require_report_selection
from app.investigations.contracts import AnnotationRequest, InvestigationCreate, InvestigationStatus, ResearchScope
from app.investigations.persistence import upgrade_investigation_schema
from app.investigations.persistence.models import ClaimRow, InvestigationRow
from app.investigations.product import policy_for, research_options, selected_policy
from app.investigations.quality import render_grounded_report
from app.investigations.repository import InvestigationRepository


def test_modes_change_resources_without_changing_evidence_rules():
    quick = selected_policy("quick", 3)
    standard = selected_policy("standard", 3)
    deep = selected_policy("deep", 3)
    assert quick["token_budget"] < standard["token_budget"] < deep["token_budget"]
    assert quick["search_results"] < standard["search_results"] < deep["search_results"]
    assert (quick["max_rework_rounds"], standard["max_rework_rounds"], deep["max_rework_rounds"]) == (0, 1, 2)
    assert all(policy["confidence_policy"] == "source-aware-v1" for policy in (quick, standard, deep))
    assert {item["id"] for item in research_options()["perspectives"]} == {"product", "purchase", "sales", "operations"}


def test_policy_snapshot_is_used_and_legacy_tasks_keep_two_rework_rounds():
    snapshot = selected_policy("quick", 2)
    assert policy_for({"policy_snapshot": snapshot}) == snapshot
    assert policy_for({})["max_rework_rounds"] == 2
    with pytest.raises(ValueError):
        selected_policy("unlimited", 2)


def test_decision_perspective_changes_report_without_inventing_facts():
    inv = {"title": "Players", "scope": {"perspective": "purchase", "decision_goal": "选择支持离线工作的工具", "competitors": ["Acme", "Beta"], "dimensions": ["离线"], "market": "China", "time_range": "2026"}}
    claims = [{"id": "c1", "competitor_id": "a", "dimension": "离线", "status": "supported", "support_basis": "official_documented", "claim_type": "fact", "text": "Acme supports offline playback.", "evidence_ids": ["e1"]}]
    data, markdown = render_grounded_report(inv, [], claims, [{"id": "e1", "title": "Docs", "source_url": "https://acme.test/docs"}], [], [{"id": "a", "name": "Acme"}, {"id": "b", "name": "Beta"}])
    assert data["decision"]["perspective"] == "purchase"
    assert "选择支持离线工作的工具" in markdown
    assert "采购选择与核对清单" in markdown
    matrix = next(section for section in data["sections"] if section["type"] == "feature_matrix")
    assert matrix["claim_ids"] == ["c1"]
    assert "Beta" in markdown and "未知" in markdown


def test_report_selection_matches_visible_markup_without_relaxing_evidence_quotes():
    from app.investigations.evidence_validation import locate_verbatim_quote

    paragraph = "- **待验证假设**：离线场景值得验证\n  前提：结论 1；验证动作：采访用户"
    require_report_selection(paragraph, "待验证假设：离线场景值得验证")
    table = "| 竞品 | 比较项目 | 结论 |\n|---|---|---|\n| Acme | 离线 | 支持下载 |"
    require_report_selection(table, "Acme\t离线\t支持下载")
    with pytest.raises(ValueError):
        require_report_selection(table, "Acme 离线 免费无限下载")
    with pytest.raises(ValueError):
        locate_verbatim_quote(paragraph, "待验证假设：离线场景值得验证")


@pytest.mark.asyncio
async def test_annotation_binds_version_quote_owner_and_budget(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'annotations.db'}")
    try:
        await upgrade_investigation_schema(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        repo = InvestigationRepository(factory)
        inv = await repo.create(
            InvestigationCreate(title="Decisions", brief="Compare players for offline usage", mode="standard", scope=ResearchScope(competitors=["Acme", "Beta"], dimensions=["离线"], perspective="purchase", decision_goal="选择离线工具")),
            user_id="owner",
        )
        competitor = (await repo.list_competitors(inv["id"], user_id="owner"))[0]
        text = "Acme supports offline playback."
        async with factory() as session, session.begin():
            (await session.get(InvestigationRow, inv["id"])).status = "synthesizing"
            session.add(
                ClaimRow(id="claim-0001", investigation_id=inv["id"], competitor_id=competitor["id"], dimension="离线", text=text, normalized_text=text.lower(), claim_type="fact", status="supported", support_basis="official_documented")
            )
        report = await repo.create_report(
            inv["id"], user_id="owner", structured_data={"sections": [{"id": "feature_matrix", "type": "feature_matrix", "markdown": text, "claim_ids": ["claim-0001"]}], "claim_versions": {"claim-0001": 1}}, rendered_markdown=text
        )
        body = AnnotationRequest(report_version=report["version"], section_key="feature_matrix", selected_text="offline playback", comment="请核对官方说明及适用版本", claim_id="claim-0001", claim_version=1, idempotency_key="annotation-key")
        assert await repo.annotations.create(inv["id"], body, user_id="stranger") is None
        with pytest.raises(ValueError, match="Report version"):
            await repo.annotations.create(inv["id"], body.model_copy(update={"report_version": 2}), user_id="owner")
        with pytest.raises(ValueError, match="Claim or report version"):
            await repo.annotations.create(inv["id"], body.model_copy(update={"claim_version": 2}), user_id="owner")
        with pytest.raises(ValueError):
            await repo.annotations.create(inv["id"], body.model_copy(update={"selected_text": "fabricated quote"}), user_id="owner")
        import asyncio

        request, replay = await asyncio.gather(repo.annotations.create(inv["id"], body, user_id="owner"), repo.annotations.create(inv["id"], body, user_id="owner"))
        assert request["id"] == replay["id"]
        assert request["target"]["claim_version"] == 1
        updated = await repo.get(inv["id"], user_id="owner")
        assert updated["status"] == "reworking"
        assert updated["token_budget"] == inv["policy_snapshot"]["refinement_tokens"]
        assert (await repo.latest_report(inv["id"], user_id="owner"))["rendered_markdown"] == text
        with pytest.raises(ValueError):
            await repo.annotations.create(inv["id"], body.model_copy(update={"comment": "different request"}), user_id="owner")
        await repo.transition(inv["id"], InvestigationStatus.CANCELLING, user_id="owner", event_type="ci.stage.progress")
        await repo.transition(inv["id"], InvestigationStatus.CANCELLED, user_id="owner", event_type="ci.stage.completed")
        assert (await repo.annotations.list(inv["id"], user_id="owner"))[0]["status"] == "cancelled"
        assert (await repo.get(inv["id"], user_id="owner"))["active_request_id"] is None
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_mode_and_decision_survive_planning_and_approval(tmp_path):
    from datetime import UTC, datetime, timedelta

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'mode.db'}")
    try:
        await upgrade_investigation_schema(engine)
        repo = InvestigationRepository(async_sessionmaker(engine, expire_on_commit=False))
        scope = ResearchScope(competitors=["Acme", "Beta"], perspective="sales", decision_goal="准备有依据的客户对比材料")
        inv = await repo.create(InvestigationCreate(title="Sales", brief="Compare solutions for customer discussions", scope=scope, mode="quick"), user_id="owner")
        assert inv["token_budget"] == 180_000
        await repo.complete_planning(inv["id"], scope, user_id="owner", run_id=None)
        approved = await repo.approve_scope(inv["id"], user_id="owner", idempotency_key="approve-mode")
        assert approved["scope"]["decision_goal"] == scope.decision_goal
        assert approved["scope"]["perspective"] == "sales"
        assert approved["resource_policy"]["max_rework_rounds"] == 0
        assert approved["deadline_at"].replace(tzinfo=UTC) > datetime.now(UTC) + timedelta(minutes=14)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_saved_source_is_scoped_to_owner_and_investigation(tmp_path):
    from datetime import UTC, datetime

    from app.investigations.contracts import EvidenceCreate
    from app.investigations.evidence_validation import sha256_text

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'snapshot.db'}")
    try:
        await upgrade_investigation_schema(engine)
        repo = InvestigationRepository(async_sessionmaker(engine, expire_on_commit=False))
        request = InvestigationCreate(title="Source", brief="Verify immutable source navigation", scope=ResearchScope(competitors=["Acme", "Beta"]))
        first = await repo.create(request, user_id="owner")
        second = await repo.create(request, user_id="owner")
        text = "Acme supports offline playback."
        evidence = await repo.add_evidence(
            first["id"],
            EvidenceCreate(
                source_url="https://acme.test/docs",
                canonical_url="https://acme.test/docs",
                source_domain="acme.test",
                title="Docs",
                retrieved_at=datetime.now(UTC),
                excerpt=text,
                snapshot_text=text,
                content_hash=sha256_text(text),
                extraction_method="direct_html",
                source_authority=20,
                freshness=8,
                extraction_quality=8,
                specificity=7,
                corroboration=0,
            ),
            user_id="owner",
        )
        assert await repo.evidence_snapshot(first["id"], evidence["id"], user_id="stranger") is None
        assert await repo.evidence_snapshot(second["id"], evidence["id"], user_id="owner") is None
        snapshot = await repo.evidence_snapshot(first["id"], evidence["id"], user_id="owner")
        assert snapshot["content"] == text and snapshot["sha256"] == sha256_text(text)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_upgrade_ci_0011_preserves_published_report_and_legacy_limits(tmp_path):
    from pathlib import Path

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import text

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'upgrade-0012.db'}")
    try:
        await upgrade_investigation_schema(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        repo = InvestigationRepository(factory)
        inv = await repo.create(InvestigationCreate(title="Existing", brief="Preserve published research during migration", scope=ResearchScope(competitors=["Acme", "Beta"])), user_id="owner")
        async with factory() as session, session.begin():
            (await session.get(InvestigationRow, inv["id"])).status = "synthesizing"
        report = await repo.create_report(inv["id"], user_id="owner", structured_data={"partial": True, "sections": []}, rendered_markdown="Immutable historical report.")
        async with engine.begin() as connection:
            await connection.execute(text("UPDATE ci_reports SET status = 'published' WHERE id = :id"), {"id": report["id"]})

            def old_schema(sync_connection):
                config = Config()
                config.set_main_option("script_location", str(Path(__file__).parents[1] / "app/investigations/persistence/migrations"))
                config.attributes["connection"] = sync_connection
                command.downgrade(config, "ci_0011")

            await connection.run_sync(old_schema)
        await upgrade_investigation_schema(engine)
        restored = await repo.get(inv["id"], user_id="owner")
        assert restored["policy_snapshot"] == {}
        assert restored["resource_policy"]["max_rework_rounds"] == 2
        assert restored["token_budget"] == inv["token_budget"]
        latest = await repo.latest_report(inv["id"], user_id="owner")
        assert latest["status"] == "published" and latest["rendered_markdown"] == report["rendered_markdown"]
        assert await repo.annotations.list(inv["id"], user_id="owner") == []
    finally:
        await engine.dispose()
