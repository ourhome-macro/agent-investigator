from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.investigations.contracts import InvestigationCreate, ResearchScope
from app.investigations.orchestration_repository import OrchestrationRepository
from app.investigations.orchestrator import DurableCompetitiveOrchestrator
from app.investigations.persistence import upgrade_investigation_schema
from app.investigations.protocols import AgentReceipt, ReceiptStatus, StageName, SubmissionKind
from app.investigations.providers import SearchHit, SourceDocument, SourceType
from app.investigations.repository import InvestigationRepository


def _receipt(task, *, run_id=None, batch_id=None, item_id=None, model="scripted") -> AgentReceipt:
    now = datetime.now(UTC)
    return AgentReceipt(
        task_id=task.task_id,
        investigation_id=task.investigation_id,
        workflow_run_id=task.workflow_run_id,
        stage=task.stage,
        item_key=task.item_key,
        role=task.role,
        status=ReceiptStatus.SUCCEEDED,
        attempt=1,
        submission_kind={
            StageName.PLANNING: SubmissionKind.SCOPE,
            StageName.COLLECTING: SubmissionKind.EVIDENCE,
            StageName.ANALYZING: SubmissionKind.CLAIMS,
            StageName.AUDITING: SubmissionKind.AUDIT,
            StageName.SYNTHESIZING: SubmissionKind.REPORT,
        }.get(task.stage, SubmissionKind.EVIDENCE),
        run_id=run_id,
        durable_batch_id=batch_id,
        durable_batch_item_id=item_id,
        model_name=model,
        token_usage={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        started_at=now,
        completed_at=now,
    )


class ScriptedProviders:
    def __init__(self, missing_initial_beta=False, one_primary=False):
        self.missing_initial_beta = missing_initial_beta
        self.one_primary = one_primary
        self.queries = []

    async def search(self, query: str, *, max_results: int):
        self.queries.append(query)
        competitor = query.split('"')[1]
        if self.missing_initial_beta and competitor == "Beta" and "official documentation" not in query:
            return []
        return [
            SearchHit(
                title=f"{competitor} documentation {index}",
                url=f"https://{competitor.casefold()}-{index}.example/docs",
                content=f"{competitor} supports audited enterprise workflows.",
                provider="scripted",
            )
            for index in range(1 if self.one_primary else max_results)
        ]

    async def fetch_url(self, url: str, *, fallback_content: str, investigation_id: str):
        del investigation_id
        competitor = url.split("//")[1].split("-")[0].capitalize()
        return SourceDocument(
            url=url,
            content=f"Immutable source for {url}. {competitor} supports audited enterprise workflows.",
            source_type=SourceType.DOCUMENTATION,
            extraction_method="scripted",
        )


class ScriptedBatches:
    def __init__(self, orchestration: OrchestrationRepository, user_id: str, overclaim=False) -> None:
        self._orchestration = orchestration
        self._user_id = user_id
        self.overclaim = overclaim

    async def submit(self, *, stage_attempt_id, tasks, **kwargs):
        del kwargs
        return {"id": f"batch-{stage_attempt_id}", "tasks": tasks}

    async def wait(self, *, batch_id, stage_attempt_id, tasks, **kwargs):
        del kwargs
        submissions = []
        receipts = []
        for task in tasks:
            if task.stage in {StageName.COLLECTING, StageName.REWORKING}:
                competitor = task.input["competitor"]
                quote = f"{competitor} supports audited enterprise workflows."
                payload = {
                    "evidence": [
                        {
                            "url": task.input["search_hits"][index - 1]["url"],
                            "title": f"{competitor} source {index}",
                            "excerpt": quote,
                            "source_type": "documentation",
                            "language": "en-US",
                            "dimension": "功能",
                        }
                        for index in range(1, min(2, len(task.input["search_hits"])) + 1)
                    ]
                }
                kind = SubmissionKind.EVIDENCE
            else:
                chunks = task.input["evidence_chunks"]
                selected = []
                seen_domains = set()
                for chunk in chunks:
                    if chunk["source_domain"] in seen_domains:
                        continue
                    seen_domains.add(chunk["source_domain"])
                    selected.append(chunk)
                    if len(selected) == 2:
                        break
                payload = {
                    "claims": [
                        {
                            "dimension": task.input["dimensions"][0],
                            "statement": {"subject": task.input["competitor"], "predicate": "supports", "object": "all enterprise workflows." if self.overclaim and task.input["competitor"] == "Acme" else "audited enterprise workflows."},
                            "material": True,
                            "claim_type": "fact",
                            "evidence_bindings": [
                                {
                                    "evidence_id": chunk["evidence_id"],
                                    "relation": "supports",
                                    "verbatim_quote": chunk["content"],
                                    "snapshot_sha256": chunk["snapshot_sha256"],
                                }
                                for chunk in selected
                            ],
                            "price_observations": [],
                        }
                    ]
                }
                kind = SubmissionKind.CLAIMS
                if not chunks:
                    payload = {"claims": []}
            submission = await self._orchestration.submit_domain_submission(
                task_id=task.task_id,
                user_id=self._user_id,
                kind=kind,
                payload=payload,
            )
            receipt = _receipt(task, batch_id=batch_id, item_id=f"item-{task.task_id}")
            await self._orchestration.update_stage_item(
                stage_attempt_id,
                item_key=task.item_key,
                status="succeeded",
                attempt=1,
                submission=submission,
                receipt=receipt,
            )
            submissions.append(submission)
            receipts.append(receipt)
        return submissions, receipts


class ScriptedRuns:
    def __init__(self, orchestration: OrchestrationRepository, user_id: str) -> None:
        self._orchestration = orchestration
        self._user_id = user_id

    async def execute(self, task, *, on_run_started, **kwargs):
        del kwargs
        run_id = f"run-{task.task_id}"
        await on_run_started(run_id)
        if task.stage == StageName.PLANNING:
            kind = SubmissionKind.SCOPE
            payload = {"scope": task.input["user_scope"]}
        elif task.stage == StageName.AUDITING:
            kind = SubmissionKind.AUDIT
            payload = {
                "binding_verdicts": [
                    {
                        "claim_id": claim["id"],
                        "evidence_id": binding["evidence_id"],
                        "relation": binding["relation"],
                        "verdict": "partially_supports" if "all enterprise" in claim["text"] else "entails",
                        "reason": "The exact quote directly states the capability.",
                    }
                    for claim in task.input["claims"]
                    for binding in claim["evidence_bindings"]
                ],
                "issues": [
                    {"claim_id": claim["id"], "rule": "overgeneralization", "reason": "The quote only proves audited workflows", "required_action": "revise", "severity": "error"}
                    for claim in task.input["claims"]
                    if "all enterprise" in claim["text"]
                ],
                "claim_verdicts": [{"claim_id": claim["id"], "atomic": True} for claim in task.input["claims"]],
                "resolutions": [
                    {"issue_id": issue["id"], "claim_version": 1, "reason": "Original claim was superseded by a narrower re-audited proposition"}
                    for issue in task.input.get("open_issues", [])
                    if issue["claim_id"] in {claim["id"] for claim in task.input.get("retired_claims", [])}
                ],
            }
        elif task.stage == StageName.REWORKING:
            kind = SubmissionKind.CLAIMS
            original = task.input["claim"]
            payload = {
                "claims": [
                    {
                        "dimension": original["dimension"],
                        "statement": {"subject": task.input["competitor"], "predicate": "supports", "object": "audited enterprise workflows."},
                        "material": True,
                        "claim_type": "fact",
                        "evidence_bindings": [{key: value for key, value in binding.items() if key in {"evidence_id", "relation", "verbatim_quote", "snapshot_sha256"}} for binding in original["evidence_bindings"]],
                    }
                ]
            }
        else:
            kind = SubmissionKind.REPORT
            payload = {
                "sections": [
                    {
                        "id": section_type,
                        "type": section_type,
                        "title": section_type.replace("_", " ").title(),
                        "claim_ids": [claim["id"] for claim in task.input["claims"]],
                        "hypotheses": [
                            {"hypothesis": "Pilot an audited workflow integration", "premise_claim_ids": [claim["id"] for claim in task.input["claims"]], "validation": "Interview target users and validate a small workflow prototype"}
                        ]
                        if section_type == "opportunities"
                        else [],
                        "evidence_ids": [item["id"] for item in task.input["evidence"]],
                    }
                    for section_type in task.input["required_section_types"]
                ]
            }
        submission = await self._orchestration.submit_domain_submission(
            task_id=task.task_id,
            user_id=self._user_id,
            kind=kind,
            payload=payload,
        )
        return submission, _receipt(task, run_id=run_id)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "missing_initial_beta,competitor_names,overclaim,one_primary,optional_gap",
    [
        (False, ["Acme", "Beta"], False, False, False),
        (True, ["Acme", "Beta"], False, False, False),
        (False, ["Acme", "Beta", "Gamma", "Delta", "Epsilon"], False, False, False),
        (False, ["Acme", "Beta"], True, False, False),
        (False, ["Acme", "Beta"], False, True, False),
        (False, ["Acme", "Beta"], False, True, True),
    ],
)
async def test_durable_workflow_reaches_publish_review_with_verified_quotes(tmp_path, missing_initial_beta, competitor_names, overclaim, one_primary, optional_gap) -> None:
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'durable-e2e.db'}")
    try:
        await upgrade_investigation_schema(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        investigations = InvestigationRepository(factory)
        orchestration = OrchestrationRepository(factory)
        user_id = "user-1"
        owner_id = "orchestrator-1"
        created = await investigations.create(
            InvestigationCreate(
                title="Acme vs Beta",
                brief="Compare enterprise workflow capabilities for a buying decision.",
                scope=ResearchScope(competitors=competitor_names, dimensions=["功能", "定位"] if optional_gap else ["功能"], official_domains={name: [f"{name.casefold()}-0.example"] for name in competitor_names} if one_primary else {}),
            ),
            user_id=user_id,
        )
        providers = ScriptedProviders(missing_initial_beta, one_primary)
        orchestrator = DurableCompetitiveOrchestrator(
            investigations=investigations,
            orchestration=orchestration,
            batches=ScriptedBatches(orchestration, user_id, overclaim),
            runs=ScriptedRuns(orchestration, user_id),
            providers=providers,
            owner_id=owner_id,
        )
        planning = await orchestration.ensure_workflow(created["id"], user_id=user_id, idempotency_key=f"{created['id']}:planning")
        planning = await orchestration.claim_workflow(planning["id"], owner_id=owner_id, lease_seconds=120)
        assert planning is not None
        await orchestrator.execute(planning, user_id=user_id)
        await orchestration.finalize_workflow(planning["id"], owner_id=owner_id, succeeded=True)
        planned = await investigations.get(created["id"], user_id=user_id)
        assert planned is not None and planned["status"] == "awaiting_scope_approval"
        await investigations.approve_scope(created["id"], user_id=user_id, idempotency_key="approve-scope")
        execution = await orchestration.ensure_workflow(created["id"], user_id=user_id, idempotency_key=f"{created['id']}:execution:round:0")
        execution = await orchestration.claim_workflow(execution["id"], owner_id=owner_id, lease_seconds=120)
        assert execution is not None
        await orchestrator.execute(execution, user_id=user_id)
        await orchestration.finalize_workflow(execution["id"], owner_id=owner_id, succeeded=True)

        completed = await investigations.get(created["id"], user_id=user_id)
        claims = await investigations.list_claims(created["id"], user_id=user_id)
        report = await investigations.latest_report(created["id"], user_id=user_id)
        assert completed is not None and completed["status"] == "awaiting_publish_approval"
        assert claims is not None
        active = [claim for claim in claims if claim["status"] != "superseded"]
        assert all(claim["status"] == "supported" for claim in active)
        assert all(binding["validation_status"] == "verified" for claim in active for binding in claim["evidence_bindings"])
        assert all(binding["entailment_status"] == "entails" for claim in active for binding in claim["evidence_bindings"])
        assert report is not None and len(report["structured_data"]["sections"]) == 11
        assert report["structured_data"]["partial"] is False
        assert "| Acme |" in report["rendered_markdown"] and "| Beta |" in report["rendered_markdown"]
        assert completed["token_reserved"] == 0
        assert completed["token_used"] <= completed["token_budget"]
        if missing_initial_beta:
            assert any('"Beta"' in query and "official documentation" in query for query in providers.queries)
        if overclaim:
            assert any(claim["status"] == "superseded" for claim in claims)
            assert "all enterprise" not in report["rendered_markdown"]
        if one_primary:
            assert all(claim["support_basis"] == "official_documented" for claim in active)
            assert completed["rework_round"] == 0
            assert not any("official documentation" in query for query in providers.queries)
        if optional_gap:
            assert report["structured_data"]["completion_status"] == "completed_with_gaps"
    finally:
        await engine.dispose()
