"""Synthetic live-runtime adapters verify that feedback actually re-enters research."""

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_investigation_durable_e2e import ScriptedBatches, ScriptedProviders, ScriptedRuns, _receipt

from app.investigations.contracts import AnnotationRequest, InvestigationCreate, ResearchScope
from app.investigations.orchestration_repository import OrchestrationRepository
from app.investigations.orchestrator import DurableCompetitiveOrchestrator
from app.investigations.persistence import upgrade_investigation_schema
from app.investigations.persistence.models import ReportRow
from app.investigations.protocols import StageName, SubmissionKind
from app.investigations.repository import InvestigationRepository


class FollowupProviders(ScriptedProviders):
    async def search(self, query, *, max_results):
        hits = await super().search(query, max_results=max_results)
        if "版本" in query:
            from dataclasses import replace

            hits = [replace(hit, url=hit.url + "/updated") for hit in hits]
        return hits


class FollowupRuns(ScriptedRuns):
    resolve_feedback = True

    async def execute(self, task, *, on_run_started, **kwargs):
        if task.stage != StageName.AUDITING or not task.input.get("user_question"):
            return await super().execute(task, on_run_started=on_run_started, **kwargs)
        await on_run_started(f"run-{task.task_id}")
        payload = {
            "binding_verdicts": [
                {"claim_id": claim["id"], "evidence_id": binding["evidence_id"], "relation": binding["relation"], "verdict": "entails", "reason": "Updated page directly supports this statement"}
                for claim in task.input["claims"]
                for binding in claim["evidence_bindings"]
            ],
            "issues": [],
            "claim_verdicts": [{"claim_id": claim["id"], "atomic": True} for claim in task.input["claims"]],
            "resolutions": [{"issue_id": issue["id"], "claim_version": 1, "reason": "The targeted updated source was fetched and checked"} for issue in task.input["open_issues"]] if self.resolve_feedback else [],
        }
        submission = await self._orchestration.submit_domain_submission(task_id=task.task_id, user_id=self._user_id, kind=SubmissionKind.AUDIT, payload=payload)
        return submission, _receipt(task, run_id=f"run-{task.task_id}")


@pytest.mark.asyncio
@pytest.mark.parametrize("resolved,gap", [(True, False), (False, False), (True, True)])
async def test_annotation_recollects_and_reaudits_only_its_target_preserving_published_version(tmp_path, resolved, gap):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'annotation-e2e.db'}")
    try:
        await upgrade_investigation_schema(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        repo, orchestration = InvestigationRepository(factory), OrchestrationRepository(factory)
        scope = ResearchScope(competitors=["Acme", "Beta"], dimensions=["功能", "workflows"] if gap else ["功能"], official_domains={"Acme": ["acme-0.example"], "Beta": ["beta-0.example"]} if gap else {})
        inv = await repo.create(InvestigationCreate(title="Compare", brief="Compare products for an enterprise decision", scope=scope), user_id="user-1")
        providers = FollowupProviders(one_primary=gap)
        runs = FollowupRuns(orchestration, "user-1")
        runs.resolve_feedback = resolved
        orchestrator = DurableCompetitiveOrchestrator(investigations=repo, orchestration=orchestration, batches=ScriptedBatches(orchestration, "user-1"), runs=runs, providers=providers, owner_id="worker")

        async def run(key):
            workflow = await orchestration.ensure_workflow(inv["id"], user_id="user-1", idempotency_key=key)
            claimed = await orchestration.claim_workflow(workflow["id"], owner_id="worker", lease_seconds=120)
            await orchestrator.execute(claimed, user_id="user-1")
            await orchestration.finalize_workflow(workflow["id"], owner_id="worker", succeeded=True)

        await run("planning")
        await repo.approve_scope(inv["id"], user_id="user-1", idempotency_key="approve-scope")
        await run("initial-research")
        original = await repo.latest_report(inv["id"], user_id="user-1")
        await repo.approve_report(inv["id"], original["version"], user_id="user-1", idempotency_key="publish-first")
        claims = await repo.list_claims(inv["id"], user_id="user-1")
        acme = next(claim for claim in claims if claim["text"].startswith("Acme"))
        beta = next(claim for claim in claims if claim["text"].startswith("Beta"))
        before_queries = len(providers.queries)
        request = await repo.annotations.create(
            inv["id"],
            AnnotationRequest(
                report_version=1,
                section_key="feature_matrix",
                selected_text="" if gap else "audited enterprise workflows",
                comment="请核对新版本的官方资料",
                idempotency_key="followup-version",
                **({"competitor_id": acme["competitor_id"], "dimension": "workflows"} if gap else {"claim_id": acme["id"], "claim_version": acme["version"]}),
            ),
            user_id="user-1",
        )
        await run("annotation-research")
        queries = providers.queries[before_queries:]
        assert queries and all('"Acme"' in query for query in queries)
        latest = await repo.latest_report(inv["id"], user_id="user-1")
        assert latest["version"] == 2 and latest["status"] == "review"
        assert latest["structured_data"]["refinement"]["request_id"] == request["id"]
        assert (await repo.annotations.list(inv["id"], user_id="user-1"))[0]["status"] == ("completed" if resolved else "needs_review")
        assert latest["structured_data"]["partial"] is (not resolved)
        refreshed = await repo.list_claims(inv["id"], user_id="user-1")
        assert next(claim for claim in refreshed if claim["id"] == beta["id"])["evidence_bindings"] == beta["evidence_bindings"]
        if gap:
            assert any(claim["dimension"] == "workflows" and claim["competitor_id"] == acme["competitor_id"] and claim["publication_eligible"] for claim in refreshed)
        else:
            assert len(next(claim for claim in refreshed if claim["id"] == acme["id"])["evidence_ids"]) > len(acme["evidence_ids"])
        async with factory() as session:
            old = await session.get(ReportRow, original["id"])
            assert old.status == "published" and old.rendered_markdown == original["rendered_markdown"]
        assert (await repo.get(inv["id"], user_id="user-1"))["token_reserved"] == 0
    finally:
        await engine.dispose()
