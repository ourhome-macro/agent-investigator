from __future__ import annotations

import asyncio
import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from app.investigations.batch_adapter import DurableStageBatchAdapter
from app.investigations.confidence import is_official_source, issue_is_blocking
from app.investigations.contracts import AtomicStatement, ClaimCreate, ClaimEvidenceRelation, EvidenceCreate, InvestigationStatus, ResearchScope
from app.investigations.evidence_validation import locate_verbatim_quote
from app.investigations.orchestration_repository import OrchestrationRepository
from app.investigations.product import decision_context, execution_cap, policy_for
from app.investigations.protocols import DomainSubmission, ReceiptStatus, StageName, StageTask, SubmissionKind
from app.investigations.providers import ResearchProviderRegistry, canonicalize_url
from app.investigations.quality import admit_candidate, balance_candidates, candidate_excerpt, claim_is_eligible, coverage_cells, render_grounded_report
from app.investigations.repository import InvestigationConflict, InvestigationRepository
from app.investigations.run_adapter import DeerFlowRunStageAdapter


class StageAcceptanceError(RuntimeError):
    pass


def _stable_task_id(idempotency_key: str) -> str:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"deerflow:competitive-research:{idempotency_key}").hex


class DurableCompetitiveOrchestrator:
    """Code-controlled Competitive Research stages backed by DeerFlow batches."""

    def __init__(
        self,
        *,
        investigations: InvestigationRepository,
        orchestration: OrchestrationRepository,
        batches: DurableStageBatchAdapter,
        runs: DeerFlowRunStageAdapter,
        providers: ResearchProviderRegistry,
        owner_id: str,
    ) -> None:
        self._investigations = investigations
        self._orchestration = orchestration
        self._batches = batches
        self._runs = runs
        self._providers = providers
        self._owner_id = owner_id

    async def execute(self, workflow: dict[str, Any], *, user_id: str) -> None:
        investigation_id = workflow["investigation_id"]
        investigation = await self._require_investigation(investigation_id, user_id)
        annotation = await self._investigations.annotations.active(investigation_id, user_id=user_id)
        if annotation:
            await self._execute_annotation(workflow, investigation, annotation, user_id=user_id)
            return
        if investigation["status"] == InvestigationStatus.PLANNING.value:
            await self._plan(workflow, investigation, user_id=user_id)
            return
        if investigation["status"] == InvestigationStatus.COLLECTING.value:
            await self._collect(workflow, investigation, user_id=user_id)
            await self._investigations.transition(
                investigation_id,
                InvestigationStatus.NORMALIZING,
                user_id=user_id,
                event_type="ci.stage.completed",
                payload={"orchestrator": self._owner_id},
            )

        investigation = await self._require_investigation(investigation_id, user_id)
        if investigation["status"] == InvestigationStatus.NORMALIZING.value:
            await self._investigations.transition(
                investigation_id,
                InvestigationStatus.ANALYZING,
                user_id=user_id,
                event_type="ci.stage.started",
            )

        investigation = await self._require_investigation(investigation_id, user_id)
        if investigation["status"] == InvestigationStatus.ANALYZING.value:
            await self._analyze(workflow, investigation, user_id=user_id)
            await self._investigations.transition(
                investigation_id,
                InvestigationStatus.AUDITING,
                user_id=user_id,
                event_type="ci.stage.started",
            )

        while True:
            investigation = await self._require_investigation(investigation_id, user_id)
            if investigation["status"] == InvestigationStatus.REWORKING.value:
                issues = await self._investigations.list_audit_issues(investigation_id, user_id=user_id, status="open") or []
                await self._rework(workflow, investigation, issues, user_id=user_id)
                await self._investigations.transition(investigation_id, InvestigationStatus.ANALYZING, user_id=user_id, event_type="ci.stage.started")
                await self._analyze(workflow, await self._require_investigation(investigation_id, user_id), user_id=user_id)
                await self._investigations.transition(investigation_id, InvestigationStatus.AUDITING, user_id=user_id, event_type="ci.stage.started")
                continue
            if investigation["status"] == InvestigationStatus.AUDITING.value:
                issues = await self._audit(workflow, investigation, user_id=user_id)
                if issues and investigation["rework_round"] < policy_for(investigation)["max_rework_rounds"]:
                    await self._investigations.begin_audit_rework(
                        investigation_id,
                        user_id=user_id,
                        issue_ids=[issue["id"] for issue in issues],
                    )
                    continue
                await self._investigations.transition(
                    investigation_id,
                    InvestigationStatus.SYNTHESIZING,
                    user_id=user_id,
                    event_type="ci.stage.started",
                    payload={"open_audit_issues": len(issues), "rework_exhausted": bool(issues)},
                )
                continue
            if investigation["status"] == InvestigationStatus.SYNTHESIZING.value:
                await self._synthesize(workflow, investigation, user_id=user_id)
            return

    async def _execute_annotation(self, workflow, investigation, annotation, *, user_id):
        investigation_id = investigation["id"]
        question = annotation["payload"]["comment"] + ("\n报告选段：" + annotation["payload"]["selected_text"] if annotation["payload"].get("selected_text") else "")
        target = {**annotation["target"], "comment": question}
        await self._investigations.annotations.mark(investigation_id, user_id=user_id, status="running")
        try:
            while True:
                investigation = await self._require_investigation(investigation_id, user_id)
                if investigation["status"] == "reworking":
                    await self._rework(workflow, investigation, [], user_id=user_id, annotation=annotation)
                    await self._investigations.transition(investigation_id, InvestigationStatus.ANALYZING, user_id=user_id, event_type="ci.stage.started")
                    continue
                if investigation["status"] == "analyzing":
                    await self._analyze(workflow, investigation, user_id=user_id, target=target)
                    await self._investigations.transition(investigation_id, InvestigationStatus.AUDITING, user_id=user_id, event_type="ci.stage.started")
                    continue
                if investigation["status"] == "auditing":
                    await self._audit(workflow, investigation, user_id=user_id, target=target)
                    if target.get("claim_id") is None:
                        claims = await self._investigations.list_claims(investigation_id, user_id=user_id) or []
                        issues = await self._investigations.list_audit_issues(investigation_id, user_id=user_id, status="open") or []
                        cells = coverage_cells([{"id": target["competitor_id"], "name": "target"}], [target["dimension"]], claims, issues)
                        await self._investigations.annotations.resolve_gap(investigation_id, user_id=user_id, request_id=annotation["id"], covered=bool(cells and cells[0]["status"] == "covered"))
                    issues = await self._investigations.list_audit_issues(investigation_id, user_id=user_id, status="open") or []
                    pending = [issue for issue in issues if issue.get("raised_by") == f"annotation:{annotation['id']}"]
                    if pending and investigation["rework_round"] < policy_for(investigation)["max_rework_rounds"]:
                        await self._investigations.begin_audit_rework(investigation_id, user_id=user_id, issue_ids=[issue["id"] for issue in pending])
                        continue
                    await self._investigations.transition(investigation_id, InvestigationStatus.SYNTHESIZING, user_id=user_id, event_type="ci.stage.started")
                    continue
                if investigation["status"] == "synthesizing":
                    await self._synthesize(workflow, investigation, user_id=user_id)
                return
        except Exception as exc:
            await self._investigations.annotations.mark(investigation_id, user_id=user_id, status="failed", error=str(exc)[:1000])
            raise

    async def _plan(self, workflow: dict[str, Any], investigation: dict[str, Any], *, user_id: str) -> None:
        task = self._task(
            workflow,
            investigation,
            stage=StageName.PLANNING,
            item_key="scope-draft",
            role="research-director",
            input_data={
                "title": investigation["title"],
                "brief": investigation["brief"],
                "user_scope": investigation["scope"],
            },
            acceptance=[
                "Keep 2-5 named competitors and explain no conclusions at planning time.",
                "Preserve the requested market, audience, language, and time range unless clearly inconsistent.",
                "Return payload.scope with market, audience, language, time_range, competitors, and dimensions.",
                "Include official_domains only when a competitor's official domain is unambiguous.",
            ],
        )
        submission = await self._execute_run_task(
            workflow,
            task,
            user_id=user_id,
            instruction=(
                "Act as the Research Director and Scope Mapper. Refine the proposed competitive-research scope. "
                "Do not use web search in Planning; keep the named competitors unless the input is structurally invalid. "
                "Return kind='scope' with payload.scope. Competitors must be plain strings, and official_domains "
                "values must contain hostnames only, never repository paths."
            ),
        )
        if submission is None:
            return
        try:
            scope = ResearchScope.model_validate(submission.payload.get("scope"))
            scope = ResearchScope.model_validate(
                {
                    **scope.model_dump(),
                    "required_dimensions": investigation["scope"].get("required_dimensions", []),
                    "perspective": investigation["scope"].get("perspective", "product"),
                    "decision_goal": investigation["scope"].get("decision_goal", investigation["brief"]),
                }
            )
        except ValueError as exc:
            raise StageAcceptanceError(f"Planning returned an invalid research scope: {exc}") from exc
        await self._investigations.complete_planning(investigation["id"], scope, user_id=user_id, run_id=None)

    async def _collect(self, workflow: dict[str, Any], investigation: dict[str, Any], *, user_id: str) -> None:
        competitors = await self._investigations.list_competitors(investigation["id"], user_id=user_id) or []
        dimensions = investigation["scope"]["dimensions"]
        queries = [(competitor, dimension) for competitor in competitors for dimension in dimensions]
        searches = await asyncio.gather(
            *[
                self._providers.search(
                    f'"{competitor["name"]}" {dimension} {investigation["scope"]["time_range"]}',
                    max_results=policy_for(investigation)["search_results"],
                )
                for competitor, dimension in queries
            ],
            return_exceptions=True,
        )
        tasks: list[StageTask] = []
        for competitor in competitors:
            hits = []
            for (target, dimension), search_result in zip(queries, searches, strict=True):
                if target["id"] == competitor["id"] and not isinstance(search_result, BaseException):
                    prepared = await asyncio.gather(*[self._prepare_candidate(investigation, competitor, dimension, hit, user_id=user_id) for hit in search_result], return_exceptions=True)
                    hits.extend(item for item in prepared if isinstance(item, dict))
            tasks.append(
                self._task(
                    workflow,
                    investigation,
                    stage=StageName.COLLECTING,
                    item_key=f"{competitor['id']}:all-angles",
                    role="competitor-collector",
                    input_data={
                        "brief": investigation["brief"],
                        "market": investigation["scope"]["market"],
                        "time_range": investigation["scope"]["time_range"],
                        "competitor_id": competitor["id"],
                        "competitor": competitor["name"],
                        "official_domains": competitor.get("official_domains", []),
                        "official_repositories": competitor.get("official_repositories", []),
                        "dimensions": dimensions,
                        "search_hits": balance_candidates(hits, dimensions, limit=policy_for(investigation)["candidate_limit"]),
                        "max_evidence": 10,
                    },
                    acceptance=[
                        "Return at least two Evidence records from distinct domains with absolute source URLs.",
                        "Use only URLs and excerpts present in search_hits; never invent a candidate.",
                        "Do not invent publication dates, prices, or metrics.",
                    ],
                )
            )
        attempt = await self._orchestration.start_stage(workflow["id"], owner_id=self._owner_id, stage=StageName.COLLECTING, tasks=tasks)
        if attempt["status"] == "completed":
            return
        await self._guard_attempt_budget(attempt["id"], investigation, tasks, user_id=user_id)
        batch = await self._batches.submit(
            stage_attempt_id=attempt["id"],
            tasks=tasks,
            user_id=user_id,
            model_name="deepseek-v4-flash",
            instruction=(
                "Act as a competitor evidence curator. Do not search or fetch. Select the strongest candidates already "
                "provided in search_hits; the server will fetch and snapshot their URLs. Return kind='evidence' with "
                "payload.evidence as a list of objects containing url, title, excerpt, source_type, publisher, "
                "published_at when known, dimension copied from its search hit, and language. Select exact excerpts about the named product and question."
            ),
        )
        submissions, receipts = await self._batches.wait(
            batch_id=batch["id"],
            stage_attempt_id=attempt["id"],
            tasks=tasks,
            user_id=user_id,
            timeout_seconds=self._remaining_seconds(investigation),
        )
        await self._record_receipts(investigation["id"], workflow["id"], receipts, user_id=user_id, stage_attempt_id=attempt["id"])
        accepted = await self._apply_evidence(investigation["id"], submissions, tasks, user_id=user_id)
        successful_items = sum(receipt.status.value == "succeeded" for receipt in receipts)
        required_items = max(1, (len(tasks) * 3 + 4) // 5)
        # Per-product completeness belongs to Coverage and targeted rework.
        # A useful partial collection must reach that controller, not dead-end here.
        stage_ok = successful_items >= required_items and accepted > 0
        await self._orchestration.finish_stage(
            attempt["id"],
            succeeded=stage_ok,
            error=None if stage_ok else f"Collection coverage failed: items={successful_items}/{len(tasks)}, evidence={accepted}",
        )
        if not stage_ok:
            raise StageAcceptanceError("Collection produced no usable evidence or too few valid submissions")

    async def _analyze(self, workflow: dict[str, Any], investigation: dict[str, Any], *, user_id: str, target: dict | None = None) -> None:
        tasks: list[StageTask] = []
        competitors = await self._investigations.list_competitors(investigation["id"], user_id=user_id) or []
        for competitor in competitors:
            if target and competitor["id"] != target["competitor_id"]:
                continue
            dimensions = [target["dimension"]] if target else investigation["scope"]["dimensions"]
            dimension = " / ".join(dimensions)
            context = await self._investigations.retrieve_context(
                investigation["id"],
                user_id=user_id,
                query=f"{competitor['name']} {dimension}",
                limit=12,
                competitor_id=competitor["id"],
            )
            tasks.append(
                self._task(
                    workflow,
                    investigation,
                    stage=StageName.ANALYZING,
                    item_key=f"analyze:{competitor['id']}:round:{investigation['rework_round']}",
                    role=self._analysis_role(dimension),
                    input_data={
                        "brief": investigation["brief"],
                        "competitors": investigation["scope"]["competitors"],
                        "dimension": dimension,
                        "dimensions": dimensions,
                        "user_question": (target or {}).get("comment"),
                        "language": investigation["scope"]["language"],
                        "competitor_id": competitor["id"],
                        "competitor": competitor["name"],
                        "evidence_chunks": context or [],
                    },
                    acceptance=[
                        "Every factual Claim has evidence_bindings with a verbatim_quote from a provided chunk.",
                        "Every binding copies the Evidence snapshot_sha256 exactly.",
                        "Material Claims should cite two independent domains when Evidence permits.",
                        "Pricing Claims include structured price_observations; missing support remains uncertain.",
                    ],
                )
            )
        attempt = await self._orchestration.start_stage(workflow["id"], owner_id=self._owner_id, stage=StageName.ANALYZING, tasks=tasks)
        if attempt["status"] == "completed":
            return
        await self._guard_attempt_budget(attempt["id"], investigation, tasks, user_id=user_id)
        batch = await self._batches.submit(
            stage_attempt_id=attempt["id"],
            tasks=tasks,
            user_id=user_id,
            model_name="deepseek-v4-flash",
            instruction=(
                "Act as the named competitive analyst. Return kind='claims' with payload.claims. Each claim contains "
                "dimension (one of dimensions), statement {subject: exact competitor name, predicate: one verb phrase, object: one value, conditions: optional time/version/region}, "
                "material, claim_type, evidence_bindings, and price_observations. Do not combine capabilities or infer strategy. Every binding must "
                "include evidence_id, relation, an exact verbatim_quote from evidence_chunks, and snapshot_sha256. "
                "Pricing claims use claim_type='pricing' and structured observations. Never invent or normalize numbers."
                " Use claim_type='vendor_statement' for marketing assertions and 'user_report' for individual user feedback. These stay attributed, not general product facts."
                " Write propositions in the requested language, while keeping every source quote verbatim."
            ),
        )
        submissions, receipts = await self._batches.wait(
            batch_id=batch["id"],
            stage_attempt_id=attempt["id"],
            tasks=tasks,
            user_id=user_id,
            timeout_seconds=self._remaining_seconds(investigation),
        )
        await self._record_receipts(investigation["id"], workflow["id"], receipts, user_id=user_id, stage_attempt_id=attempt["id"])
        accepted = await self._apply_claims(investigation["id"], submissions, user_id=user_id, tasks=tasks)
        successful_items = sum(receipt.status.value == "succeeded" for receipt in receipts)
        required_items = max(1, (len(tasks) * 3 + 4) // 5)
        stage_ok = successful_items >= required_items and accepted > 0
        await self._orchestration.finish_stage(
            attempt["id"],
            succeeded=stage_ok,
            error=None if stage_ok else f"Analysis coverage failed: items={successful_items}/{len(tasks)}, claims={accepted}",
        )
        if not stage_ok:
            raise StageAcceptanceError("Analysis did not reach the 60% item and minimum Claim thresholds")

    async def _audit(self, workflow: dict[str, Any], investigation: dict[str, Any], *, user_id: str, target: dict | None = None) -> list[dict[str, Any]]:
        evidence = await self._investigations.list_evidence(investigation["id"], user_id=user_id) or []
        claims = [claim for claim in await self._investigations.list_claims(investigation["id"], user_id=user_id) or [] if claim["status"] not in {"superseded", "rejected"}]
        if target:
            claims = [claim for claim in claims if claim.get("competitor_id") == target["competitor_id"] and claim["dimension"] == target["dimension"]]
        audited_ids = {claim["id"] for claim in claims}
        all_findings = await self._investigations.list_claims(investigation["id"], user_id=user_id) or []
        target_ids = {claim["id"] for claim in all_findings if not target or (claim.get("competitor_id") == target["competitor_id"] and claim["dimension"] == target["dimension"])}
        open_issues = await self._investigations.list_audit_issues(investigation["id"], user_id=user_id, status="open") or []
        if target:
            open_issues = [issue for issue in open_issues if issue.get("claim_id") in target_ids or issue.get("raised_by") == f"annotation:{investigation.get('active_request_id')}"]
        prices = await self._investigations.list_price_observations(investigation["id"], user_id=user_id) or []
        competitor_by_id = {item["id"]: item for item in await self._investigations.list_competitors(investigation["id"], user_id=user_id) or []}
        deterministic = self._deterministic_audit_issues(claims, evidence)
        deterministic.extend(self._pricing_audit_issues(claims, prices))
        task = self._task(
            workflow,
            investigation,
            stage=StageName.AUDITING,
            item_key=f"evidence-audit:round-{investigation['rework_round']}",
            role="evidence-auditor",
            input_data={
                "claims": [self._compact_claim_for_audit(claim) for claim in claims[:200]],
                "language": investigation["scope"]["language"],
                "user_question": (target or {}).get("comment"),
                "retired_claims": [
                    {"id": item["id"], "version": item.get("version", 1), "status": item["status"]}
                    for item in await self._investigations.list_claims(investigation["id"], user_id=user_id) or []
                    if item["status"] in {"superseded", "rejected"}
                ],
                "evidence": [
                    {
                        "id": item["id"],
                        "domain": item["source_domain"],
                        "source_type": item["source_type"],
                        "title": item["title"],
                        "official_verified": is_official_source(
                            item["source_url"], competitor_by_id.get(item.get("competitor_id"), {}).get("official_domains", []), competitor_by_id.get(item.get("competitor_id"), {}).get("official_repositories", [])
                        ),
                    }
                    for item in evidence[:200]
                ],
                "deterministic_issues": deterministic,
                "price_observations": prices,
                "open_issues": open_issues,
            },
            acceptance=[
                "Never waive a deterministic issue unless the cited evidence directly disproves it.",
                "Check whether each excerpt actually entails its claim and flag conflicts or unsupported exact values.",
                "Return payload.issues with claim_id, severity, rule, reason, and required_action.",
            ],
        )
        audit_instruction = (
            "Act as an independent Evidence Auditor. Return kind='audit'. Treat deterministic_issues as mandatory. "
            "Add semantic-support, contradiction, exact-number, pricing-source, and source-independence issues that "
            "you can justify from the supplied records. Return payload.binding_verdicts for every Claim-Evidence "
            "binding with claim_id, evidence_id, relation, verdict (entails, partially_supports, contradicts, or "
            "unrelated), and reason. Return payload.issues as a JSON list. required_action must be one of recollect, revise, split, reject, investigate_conflict. "
            "Review atomicity: a factual Claim must express one predicate about one product with explicit conditions. Return payload.claim_verdicts for EVERY claim with claim_id and atomic (boolean). "
            "Return payload.resolutions only for explicitly rechecked old issues, each with issue_id, claim_version and reason. Omission never resolves an issue."
            " A single verified official source can establish an attributed product statement or official price. Vendor assertions and individual reports stay attributed and do not prove empirical effects or prevalence."
            " Do not demand a second domain for those attributed statements. Use error severity for blockers, warning/info for optional details. Write reasons and next actions in the requested research language."
        )
        combined = {key: [] for key in ("binding_verdicts", "issues", "claim_verdicts", "resolutions")}
        for offset in range(0, max(1, len(claims)), 4):
            group = claims[offset : offset + 4]
            group_ids = {claim["id"] for claim in group}
            evidence_ids = {eid for claim in group for eid in claim["evidence_ids"]}
            input_data = {
                **task.input,
                "claims": [self._compact_claim_for_audit(claim) for claim in group],
                "evidence": [item for item in task.input["evidence"] if item["id"] in evidence_ids],
                "deterministic_issues": [issue for issue in deterministic if issue.get("claim_id") in group_ids],
                "price_observations": [price for price in prices if price["claim_id"] in group_ids],
                "open_issues": [issue for issue in task.input["open_issues"] if issue.get("claim_id") in group_ids or (offset == 0 and issue.get("claim_id") not in {claim["id"] for claim in claims})],
            }
            chunk_task = self._task(
                workflow, investigation, stage=StageName.AUDITING, item_key=f"audit:round-{investigation['rework_round']}:chunk-{offset // 4}", role="evidence-auditor", input_data=input_data, acceptance=task.acceptance_criteria
            )
            chunk_submission = await self._execute_run_task(workflow, chunk_task, user_id=user_id, instruction=audit_instruction)
            if chunk_submission is None:
                raise StageAcceptanceError("Audit chunk did not return a validated submission")
            for key in combined:
                combined[key].extend(chunk_submission.payload.get(key, []))
        submission = chunk_submission.model_copy(update={"payload": combined})
        await self._investigations.apply_audit_verdicts(
            investigation["id"],
            [item for item in submission.payload.get("binding_verdicts", []) if isinstance(item, dict)],
            user_id=user_id,
            auditor="evidence-auditor",
            claim_ids=audited_ids,
        )
        claims = await self._investigations.list_claims(investigation["id"], user_id=user_id) or []
        claims = [claim for claim in claims if claim["status"] not in {"superseded", "rejected"}]
        if target:
            claims = [claim for claim in claims if claim["id"] in audited_ids]
        deterministic = self._deterministic_audit_issues(claims, evidence)
        deterministic.extend(self._pricing_audit_issues(claims, prices))
        allowed_issue_ids = {issue["id"] for issue in open_issues}
        await self._investigations.resolve_audit_issues(investigation["id"], [resolution for resolution in submission.payload.get("resolutions", []) if resolution.get("issue_id") in allowed_issue_ids], user_id=user_id)
        issues = list(deterministic)
        known_claim_ids = {claim["id"] for claim in claims}
        atomics = {item.get("claim_id"): item.get("atomic") for item in submission.payload.get("claim_verdicts", []) if isinstance(item, dict)}
        for claim in claims:
            if atomics.get(claim["id"]) is not True:
                issues.append({"claim_id": claim["id"], "severity": "error", "rule": "atomicity", "reason": "Atomicity has not been confirmed for this claim", "required_action": "split" if atomics.get(claim["id"]) is False else "revise"})
        for candidate in submission.payload.get("issues", [])[:200]:
            if not isinstance(candidate, dict):
                continue
            claim_id = candidate.get("claim_id")
            if claim_id is not None and claim_id not in known_claim_ids:
                continue
            issues.append(
                {
                    "claim_id": claim_id,
                    "section_id": candidate.get("section_id"),
                    "severity": candidate.get("severity") or "warning",
                    "rule": candidate.get("rule") or "semantic_support",
                    "reason": candidate.get("reason") or "Evidence does not clearly entail the claim.",
                    "required_action": candidate.get("required_action") or "Collect direct supporting evidence.",
                }
            )
        deduplicated: dict[tuple[str | None, str], dict[str, Any]] = {}
        for issue in issues:
            deduplicated[(issue.get("claim_id"), str(issue.get("rule")))] = issue
        persisted = await self._investigations.replace_audit_issues(
            investigation["id"],
            list(deduplicated.values()),
            user_id=user_id,
            raised_by="evidence-auditor",
        )
        competitors = await self._investigations.list_competitors(investigation["id"], user_id=user_id) or []
        cells = coverage_cells(competitors, investigation["scope"]["dimensions"], claims, persisted or [])
        covered_competitors = {cell["competitor_id"] for cell in cells if cell["status"] == "covered"}
        required_dimensions = set(investigation["scope"].get("required_dimensions", []))
        core_claim_ids = {claim["id"] for claim in claims if claim.get("competitor_id") not in covered_competitors or claim["dimension"] in required_dimensions}
        await self._investigations.record_control_event(investigation["id"], user_id=user_id, event_type="ci.coverage.updated", stage="auditing", payload={"cells": cells, "round": investigation["rework_round"]})
        gaps = [
            {
                "id": f"gap:{cell['competitor_id']}:{index}",
                "claim_id": None,
                "competitor_id": cell["competitor_id"],
                "dimension": cell["dimension"],
                "rule": "coverage_gap",
                "reason": "Research question lacks eligible evidence-backed claims",
                "required_action": "recollect",
            }
            for index, cell in enumerate(cells)
            if cell["status"] != "covered" and (cell["competitor_id"] not in covered_competitors or cell["dimension"] in required_dimensions)
        ]
        return [*(issue for issue in persisted or [] if issue_is_blocking(issue) and (issue.get("claim_id") is None or issue["claim_id"] in core_claim_ids)), *gaps]

    async def _rework(
        self,
        workflow: dict[str, Any],
        investigation: dict[str, Any],
        issues: list[dict[str, Any]],
        *,
        user_id: str,
        annotation: dict | None = None,
    ) -> None:
        claims = await self._investigations.list_claims(investigation["id"], user_id=user_id) or []
        claim_by_id = {claim["id"]: claim for claim in claims}
        competitors = await self._investigations.list_competitors(investigation["id"], user_id=user_id) or []
        competitor_by_id = {item["id"]: item for item in competitors}
        cells = coverage_cells(competitors, investigation["scope"]["dimensions"], claims, issues)
        covered_competitors = {cell["competitor_id"] for cell in cells if cell["status"] == "covered"}
        required_dimensions = set(investigation["scope"].get("required_dimensions", []))
        target_issues = []
        if annotation:
            issues = [
                {
                    **annotation["target"],
                    "id": annotation["id"],
                    "rule": "user_annotation",
                    "severity": "error",
                    "reason": annotation["payload"]["comment"],
                    "required_action": annotation["payload"]["action"],
                    "research_question": annotation["payload"]["comment"],
                }
            ]
        for issue in issues:
            if not issue_is_blocking(issue):
                continue
            claim = claim_by_id.get(issue.get("claim_id"))
            if annotation and claim is None and issue.get("competitor_id"):
                target_issues.append(issue)
                continue
            if claim is None or claim["status"] in {"superseded", "rejected"}:
                continue
            if not annotation and claim.get("competitor_id") in covered_competitors and claim["dimension"] not in required_dimensions:
                continue
            action = issue.get("required_action")
            if action in {"revise", "split", "reject"}:
                await self._revise_claim(workflow, investigation, issue, claim, user_id=user_id)
                continue
            target_issues.append({**issue, "competitor_id": claim.get("competitor_id"), "dimension": claim["dimension"]})
        target_issues.extend(
            {"id": f"gap-{index}", "claim_id": None, "competitor_id": cell["competitor_id"], "dimension": cell["dimension"], "rule": "coverage_gap", "reason": "Missing verified coverage", "required_action": "recollect"}
            for index, cell in enumerate(cells)
            if not annotation and cell["status"] == "missing" and (cell["competitor_id"] not in covered_competitors or cell["dimension"] in required_dimensions)
        )
        unique = {}
        for issue in target_issues:
            if issue.get("competitor_id") in competitor_by_id:
                unique.setdefault((issue["competitor_id"], issue["dimension"]), issue)
        target_issues = list(unique.values())[:5]
        searches = await asyncio.gather(
            *[
                self._providers.search(
                    f'"{competitor_by_id[issue["competitor_id"]]["name"]}" {issue["dimension"]} {investigation["scope"]["time_range"]} official documentation'
                    + (" limitations changes version differences 不支持 限制 版本差异" if issue["required_action"] == "investigate_conflict" else "")
                    + (" " + str(issue.get("research_question", ""))[:180]),
                    # The user question is data, scoped to the selected product/question.
                    max_results=policy_for(investigation)["search_results"] + 2,
                )
                for issue in target_issues
            ],
            return_exceptions=True,
        )
        tasks: list[StageTask] = []
        for issue, search_result in zip(target_issues, searches, strict=True):
            hits = [] if isinstance(search_result, BaseException) else search_result
            prepared = await asyncio.gather(*[self._prepare_candidate(investigation, competitor_by_id[issue["competitor_id"]], issue["dimension"], hit, user_id=user_id) for hit in hits], return_exceptions=True)
            hits = [item for item in prepared if isinstance(item, dict)]
            tasks.append(
                self._task(
                    workflow,
                    investigation,
                    stage=StageName.REWORKING,
                    item_key=f"round-{investigation['rework_round']}:{issue['id']}",
                    role="competitor-collector",
                    input_data={
                        "brief": investigation["brief"],
                        "competitor_id": issue["competitor_id"],
                        "competitor": competitor_by_id[issue["competitor_id"]]["name"],
                        "official_domains": competitor_by_id[issue["competitor_id"]].get("official_domains", []),
                        "official_repositories": competitor_by_id[issue["competitor_id"]].get("official_repositories", []),
                        "dimension": issue["dimension"],
                        "dimensions": [issue["dimension"]],
                        "claim_id": issue.get("claim_id"),
                        "claim": claim_by_id.get(issue.get("claim_id")),
                        "audit_rule": issue["rule"],
                        "audit_reason": issue["reason"],
                        "user_question": issue.get("research_question"),
                        "required_action": issue["required_action"],
                        "evidence_relation": "context" if issue["required_action"] == "investigate_conflict" else "supports",
                        "search_hits": hits,
                        "max_evidence": 3,
                    },
                    acceptance=[
                        "Select evidence targeted to this exact audit issue from search_hits only.",
                        "Prefer an independent primary domain not already cited by the Claim.",
                        "Return an empty evidence list when no reliable source is found.",
                    ],
                )
            )
        if not tasks:
            return
        attempt = await self._orchestration.start_stage(workflow["id"], owner_id=self._owner_id, stage=StageName.REWORKING, tasks=tasks)
        if attempt["status"] == "completed":
            return
        await self._guard_attempt_budget(attempt["id"], investigation, tasks, user_id=user_id)
        batch = await self._batches.submit(
            stage_attempt_id=attempt["id"],
            tasks=tasks,
            user_id=user_id,
            model_name="deepseek-v4-flash",
            instruction=("Act as a targeted evidence curator. Do not search or fetch. Select only candidates from search_hits that address the audit issue, then submit kind='evidence'."),
        )
        submissions, receipts = await self._batches.wait(
            batch_id=batch["id"],
            stage_attempt_id=attempt["id"],
            tasks=tasks,
            user_id=user_id,
            timeout_seconds=self._remaining_seconds(investigation),
        )
        await self._record_receipts(investigation["id"], workflow["id"], receipts, user_id=user_id, stage_attempt_id=attempt["id"])
        accepted = await self._apply_evidence(investigation["id"], submissions, tasks, user_id=user_id)
        succeeded = sum(receipt.status.value == "succeeded" for receipt in receipts)
        stage_ok = succeeded > 0 and accepted > 0
        await self._orchestration.finish_stage(
            attempt["id"],
            succeeded=stage_ok,
            error=None if stage_ok else f"All targeted rework items failed; accepted_evidence={accepted}",
        )
        if not stage_ok:
            await self._investigations.record_control_event(investigation["id"], user_id=user_id, stage="reworking", event_type="ci.rework.no_progress", payload={"accepted_evidence": accepted, "round": investigation["rework_round"]})

    async def _revise_claim(self, workflow, investigation, issue, claim, *, user_id):
        action = issue["required_action"]
        replacements = []
        if action != "reject":
            competitors = await self._investigations.list_competitors(investigation["id"], user_id=user_id) or []
            competitor_name = next((item["name"] for item in competitors if item["id"] == claim.get("competitor_id")), "")
            task = self._task(
                workflow,
                investigation,
                stage=StageName.REWORKING,
                item_key=f"revise:{claim['id']}:{investigation['rework_round']}",
                role="claim-editor",
                input_data={"claim": claim, "issue": issue, "competitor_id": claim.get("competitor_id"), "competitor": competitor_name, "dimensions": [claim["dimension"]]},
                acceptance=["Return only narrower atomic claims supported by the supplied verbatim bindings.", "For split return at least two distinct atomic claims; do not introduce new facts."],
            )
            submission = await self._execute_run_task(
                workflow,
                task,
                user_id=user_id,
                instruction=(
                    "Revise or split this claim as requested. Return kind='claims' and payload.claims. Each claim contains dimension, statement {subject, predicate, object, conditions}, "
                    "material, claim_type and evidence_bindings. Keep the original competitor and exact source quotes."
                ),
            )
            if submission is None or (action == "split" and len(submission.payload["claims"]) < 2):
                return
            await self._apply_claims(investigation["id"], [submission], user_id=user_id, tasks=[task], accepted_ids=replacements)
            replacements = list(dict.fromkeys(cid for cid in replacements if cid != claim["id"]))
            if not replacements or (action == "split" and len(replacements) < 2):
                return
        await self._investigations.retire_claim(investigation["id"], claim["id"], user_id=user_id, expected_version=claim.get("version", 1), action=action, replacement_ids=replacements)

    async def _synthesize(self, workflow: dict[str, Any], investigation: dict[str, Any], *, user_id: str) -> None:
        claims = await self._investigations.list_claims(investigation["id"], user_id=user_id) or []
        evidence = await self._investigations.list_evidence(investigation["id"], user_id=user_id) or []
        issues = await self._investigations.list_audit_issues(investigation["id"], user_id=user_id, status="open") or []
        annotation = await self._investigations.annotations.active(investigation["id"], user_id=user_id)
        if annotation:
            unresolved = any(issue.get("raised_by") == f"annotation:{annotation['id']}" for issue in issues)
            investigation = {**investigation, "annotation_unresolved": unresolved, "annotation": annotation}
        prices = await self._investigations.list_price_observations(investigation["id"], user_id=user_id) or []
        claims = [claim for claim in claims if claim_is_eligible(claim, issues)]
        competitors = await self._investigations.list_competitors(investigation["id"], user_id=user_id) or []
        task = self._task(
            workflow,
            investigation,
            stage=StageName.SYNTHESIZING,
            item_key=f"report:round-{investigation['rework_round']}",
            role="report-editor",
            input_data={
                "title": investigation["title"],
                "decision": decision_context(investigation["scope"]),
                "annotation": annotation,
                "scope": investigation["scope"],
                "claims": [
                    {
                        "id": claim["id"],
                        "dimension": claim["dimension"],
                        "text": claim.get("display_text", claim["text"]),
                        "material": claim["material"],
                        "claim_type": claim["claim_type"],
                        "support_basis": claim.get("support_basis", "unverified"),
                        "status": claim["status"],
                        "evidence_ids": claim["evidence_ids"],
                    }
                    for claim in claims[:250]
                ],
                "evidence": [{"id": item["id"], "title": item["title"], "url": item["source_url"], "excerpt": item["excerpt"][:800]} for item in evidence[:250]],
                "open_audit_issues": issues,
                "price_observations": prices,
                "required_section_types": list(self._required_report_sections()),
            },
            acceptance=[
                "Return all required section types exactly once.",
                "Select claim_ids for each section; do not submit markdown or new factual prose.",
                "Keep uncertain claims and unresolved audit issues explicit; never upgrade them to facts.",
            ],
        )
        submission = await self._execute_run_task(
            workflow,
            task,
            user_id=user_id,
            instruction=(
                "Act as the Report Editor. Return kind='report' with payload.sections. Each section has type and claim_ids only. The server renders facts and the comparison matrix. "
                "For opportunities optionally add hypotheses with hypothesis, premise_claim_ids and validation. Hypotheses must be explicit proposals, never new factual assertions. Do not return markdown."
                " Prioritize the user's decision.goal and decision.question. Product planning needs priorities, purchase needs selection checks, sales needs defensible differences, operations needs user experiments."
            ),
        )
        if submission is None:
            return
        structured, markdown = render_grounded_report(investigation, submission.payload["sections"], claims, evidence, issues, competitors)
        structured["source_task_id"] = task.task_id
        await self._investigations.create_report(investigation["id"], structured_data=structured, rendered_markdown=markdown, user_id=user_id)

    async def _execute_run_task(
        self,
        workflow: dict[str, Any],
        task: StageTask,
        *,
        user_id: str,
        instruction: str,
    ) -> DomainSubmission | None:
        while True:
            attempt = await self._orchestration.start_stage(workflow["id"], owner_id=self._owner_id, stage=task.stage, tasks=[task])
            if attempt["status"] == "completed":
                submission = await self._orchestration.get_stage_submission(attempt["id"], item_key=task.item_key)
                if submission is None:
                    raise StageAcceptanceError(f"Completed {task.stage.value} stage has no durable submission")
                submission.require_matches(task)
                return submission
            investigation = await self._require_investigation(task.investigation_id, user_id)
            await self._guard_attempt_budget(attempt["id"], investigation, [task], user_id=user_id)
            submission_tool = {
                StageName.PLANNING: "submit_scope",
                StageName.AUDITING: "submit_audit",
                StageName.SYNTHESIZING: "submit_report_section",
            }.get(task.stage)
            submission, receipt = await self._runs.execute(
                task,
                user_id=user_id,
                instruction=(instruction + (f" You MUST call {submission_tool} with task_id='{task.task_id}', then return that tool's JSON result exactly." if submission_tool else "")),
                existing_run_id=attempt.get("run_id"),
                on_run_started=lambda run_id: self._orchestration.bind_run(attempt["id"], run_id=run_id),
                timeout_seconds=self._remaining_seconds(investigation),
            )
            persisted = await self._orchestration.get_stage_submission(attempt["id"], item_key=task.item_key)
            if persisted is not None:
                persisted.require_matches(task)
                submission = persisted
                receipt.status = ReceiptStatus.SUCCEEDED
                receipt.submission_kind = persisted.kind
                receipt.error = None
            elif submission is not None:
                submission.require_matches(task)
                submission = await self._orchestration.submit_domain_submission(
                    task_id=task.task_id,
                    user_id=user_id,
                    kind=submission.kind,
                    payload=submission.payload,
                    warnings=submission.warnings,
                )
                receipt.status = ReceiptStatus.SUCCEEDED
                receipt.submission_kind = submission.kind
                receipt.error = None
            else:
                submission = None
                receipt.status = ReceiptStatus.REJECTED
                receipt.error = receipt.error or f"{task.stage.value} Agent produced no valid DomainSubmission"
            receipt.attempt = attempt.get("task_attempt", attempt["attempt"])
            await self._record_receipts(task.investigation_id, workflow["id"], [receipt], user_id=user_id, stage_attempt_id=attempt["id"])
            await self._orchestration.update_stage_item(
                attempt["id"],
                item_key=task.item_key,
                status=receipt.status.value,
                attempt=receipt.attempt,
                submission=submission,
                receipt=receipt,
                error=receipt.error,
            )
            succeeded = submission is not None
            await self._orchestration.finish_stage(attempt["id"], succeeded=succeeded, error=receipt.error)
            if succeeded:
                return submission
            if attempt.get("task_attempt", attempt["attempt"]) >= 3:
                raise StageAcceptanceError(receipt.error or f"{task.stage.value} run did not return an accepted submission")

    async def _assert_budget(self, investigation: dict[str, Any], tasks: list[StageTask], *, user_id: str) -> None:
        estimated = sum(execution_cap(investigation, task.stage.value) for task in tasks)
        await self._investigations.assert_execution_budget(
            investigation["id"],
            user_id=user_id,
            stage=tasks[0].stage.value,
            estimated_tokens=estimated,
        )

    async def _guard_attempt_budget(
        self,
        stage_attempt_id: str,
        investigation: dict[str, Any],
        tasks: list[StageTask],
        *,
        user_id: str,
    ) -> None:
        try:
            persisted_tasks = {item.task_id: item for item in await self._orchestration.get_stage_tasks(stage_attempt_id, user_id=user_id)}
            if set(persisted_tasks) != {task.task_id for task in tasks}:
                raise StageAcceptanceError("Persisted stage envelope does not match scheduled tasks")
            for task in tasks:
                task.input = persisted_tasks[task.task_id].input
            cap = execution_cap(investigation, tasks[0].stage.value)
            await self._investigations.reserve_budget(investigation["id"], user_id=user_id, stage=tasks[0].stage.value, reservation_key=stage_attempt_id, tokens=cap * len(tasks))
            for task in tasks:
                task.input["execution_token_cap"] = cap
                task.input["budget_reservation_key"] = f"{stage_attempt_id}:{task.task_id}"
        except Exception as exc:
            await self._orchestration.finish_stage(stage_attempt_id, succeeded=False, error=str(exc)[:1000])
            raise

    async def _record_receipts(
        self,
        investigation_id: str,
        workflow_run_id: str,
        receipts: list[Any],
        *,
        user_id: str,
        stage_attempt_id: str,
    ) -> None:
        actual_total = 0
        investigation = await self._require_investigation(investigation_id, user_id)
        for receipt in receipts:
            cap = execution_cap(investigation, receipt.stage.value)
            usage = receipt.token_usage or {"total_tokens": cap, "estimated": True}
            actual_total += int(usage.get("total_tokens") or usage.get("input_tokens", 0) + usage.get("output_tokens", 0))
            execution_id = receipt.run_id or receipt.durable_batch_item_id or receipt.task_id
            await self._investigations.record_token_usage(
                investigation_id,
                user_id=user_id,
                workflow_run_id=workflow_run_id,
                stage=receipt.stage.value,
                task_id=receipt.task_id,
                run_id=receipt.run_id,
                durable_batch_id=receipt.durable_batch_id,
                model_name=receipt.model_name,
                token_usage=usage,
                idempotency_key=f"{workflow_run_id}:{receipt.stage.value}:{execution_id}:{receipt.attempt}",
            )
        await self._investigations.settle_budget(investigation_id, user_id=user_id, reservation_key=stage_attempt_id, actual_tokens=actual_total, already_recorded=True)

    @staticmethod
    def _remaining_seconds(investigation: dict[str, Any]) -> float:
        deadline = investigation.get("deadline_at")
        if deadline is None:
            return 1800.0
        if isinstance(deadline, str):
            deadline = datetime.fromisoformat(deadline.replace("Z", "+00:00"))
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        return max(1.0, min(1800.0, (deadline - datetime.now(UTC)).total_seconds()))

    @staticmethod
    def _deterministic_audit_issues(claims: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
        evidence_by_id = {item["id"]: item for item in evidence}
        issues: list[dict[str, Any]] = []
        for claim in claims:
            verified_bindings = [binding for binding in claim.get("evidence_bindings", []) if binding.get("relation") == "supports" and binding.get("validation_status") == "verified" and binding.get("entailment_status") == "entails"]
            cited = [evidence_by_id[binding["evidence_id"]] for binding in verified_bindings if binding["evidence_id"] in evidence_by_id]
            domains = {item["source_domain"].lower() for item in cited}
            required = 2 if claim["material"] else 1
            unverified = [binding for binding in claim.get("evidence_bindings", []) if binding.get("relation") == "supports" and (binding.get("validation_status") != "verified" or binding.get("entailment_status") != "entails")]
            if unverified:
                issues.append(
                    {
                        "claim_id": claim["id"],
                        "severity": "error",
                        "rule": "semantic_entailment",
                        "reason": f"{len(unverified)} Claim-Evidence bindings are not verified as entails.",
                        "required_action": "Collect an exact supporting quote or downgrade the Claim to uncertain.",
                    }
                )
            if not cited:
                issues.append(
                    {
                        "claim_id": claim["id"],
                        "severity": "error",
                        "rule": "factual_claim_requires_evidence",
                        "reason": "The factual claim has no active Evidence relation.",
                        "required_action": "Collect direct evidence or remove the factual assertion.",
                    }
                )
            elif claim.get("support_basis", "unverified") == "unverified" and len(domains) < required:
                issues.append(
                    {
                        "claim_id": claim["id"],
                        "severity": "error",
                        "rule": "independent_source_threshold",
                        "reason": f"Claim has {len(domains)} independent domains; {required} required.",
                        "required_action": "Collect a corroborating source from a different primary domain.",
                    }
                )
        return issues

    @staticmethod
    def _required_report_sections() -> tuple[str, ...]:
        return (
            "executive_summary",
            "methodology",
            "landscape",
            "competitor_profiles",
            "feature_matrix",
            "pricing",
            "positioning",
            "strengths_weaknesses",
            "opportunities",
            "risks_unknowns",
            "evidence_appendix",
        )

    @staticmethod
    def _compact_claim_for_audit(claim: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": claim["id"],
            "version": claim.get("version", 1),
            "competitor_id": claim.get("competitor_id"),
            "dimension": claim["dimension"],
            "text": claim["text"],
            "material": claim["material"],
            "claim_type": claim["claim_type"],
            "support_basis": claim.get("support_basis", "unverified"),
            "status": claim["status"],
            "evidence_bindings": [
                {
                    "evidence_id": binding["evidence_id"],
                    "relation": binding["relation"],
                    "verbatim_quote": str(binding.get("verbatim_quote") or "")[:800],
                    "snapshot_sha256": binding.get("snapshot_sha256"),
                    "validation_status": binding.get("validation_status"),
                }
                for binding in claim.get("evidence_bindings", [])
            ],
        }

    @staticmethod
    def _pricing_audit_issues(claims: list[dict[str, Any]], prices: list[dict[str, Any]]) -> list[dict[str, Any]]:
        issues: list[dict[str, Any]] = []
        for claim in claims:
            explicitly_estimated = any(marker in claim["text"].casefold() for marker in ("estimate", "estimated", "第三方估计", "估算"))
            if claim["claim_type"] == "pricing" and not explicitly_estimated and not any(price["claim_id"] == claim["id"] and price["official"] for price in prices):
                issues.append(
                    {
                        "claim_id": claim["id"],
                        "severity": "warning",
                        "rule": "pricing_official_source",
                        "reason": "Pricing Claim has no official-domain PriceObservation.",
                        "required_action": "Collect the official pricing page or label the value as a third-party estimate.",
                    }
                )
        return issues

    async def _prepare_candidate(self, investigation, competitor, dimension, hit, *, user_id):
        url = canonicalize_url(hit.url)
        try:
            if self._remaining_seconds(investigation) <= 0:
                raise StageAcceptanceError("Research deadline expired during candidate extraction")
            document = await asyncio.wait_for(self._providers.fetch_url(url, fallback_content="", investigation_id=investigation["id"]), timeout=min(60, self._remaining_seconds(investigation)))
            excerpt = candidate_excerpt(document.content, dimension)
            decision = admit_candidate(
                url=url,
                offered_urls=[url],
                competitor=competitor["name"],
                dimension=dimension,
                title=hit.title,
                excerpt=excerpt,
                official_domains=competitor.get("official_domains", []),
                official_repositories=competitor.get("official_repositories", []),
            )
            accepted = decision.accepted and document.extraction_method != "search_snippet"
            payload = {
                "url": url,
                "title": hit.title,
                "dimension": dimension,
                "competitor_id": competitor["id"],
                "excerpt": excerpt,
                "content": document.content,
                "content_hash": hashlib.sha256(document.content.encode("utf-8")).hexdigest(),
                "extraction_method": document.extraction_method,
                "source_type": document.source_type.value,
                "mime_type": document.mime_type,
                "admitted": accepted,
                "reason": decision.reason,
            }
            cid = await self._investigations.save_candidate(investigation["id"], user_id=user_id, payload=payload)
            await self._investigations.record_control_event(
                investigation["id"],
                user_id=user_id,
                event_type="ci.candidate.assessed",
                stage=investigation["status"],
                payload={"candidate_id": cid, "url": url, "competitor_id": competitor["id"], "dimension": dimension, "accepted": accepted, "reason": decision.reason},
            )
            if accepted:
                return {"candidate_id": cid, "url": url, "title": hit.title, "excerpt": excerpt, "dimension": dimension}
        except Exception as exc:
            await self._investigations.record_control_event(investigation["id"], user_id=user_id, event_type="ci.candidate.failed", stage=investigation["status"], payload={"url": url, "reason": str(exc)[:400]})
        return None

    async def _apply_evidence(
        self,
        investigation_id: str,
        submissions: list[DomainSubmission],
        tasks: list[StageTask],
        *,
        user_id: str,
    ) -> int:
        task_by_key = {task.item_key: task for task in tasks}
        accepted = 0
        known_ids = {item["id"] for item in await self._investigations.list_evidence(investigation_id, user_id=user_id) or []}
        for submission in submissions:
            if submission.kind is not SubmissionKind.EVIDENCE:
                continue
            task = task_by_key[submission.item_key]
            for candidate in submission.payload.get("evidence", [])[:10]:
                if not isinstance(candidate, dict):
                    continue
                try:
                    canonical = canonicalize_url(str(candidate["url"]))
                    excerpt = str(candidate.get("excerpt") or "").strip()
                    hits = [hit for hit in task.input.get("search_hits", []) if canonicalize_url(hit["url"]) == canonical]
                    dimension = str(candidate.get("dimension") or task.input.get("dimension") or (hits[0].get("dimension") if hits else "") or "")
                    offered = [hit["url"] for hit in hits if hit.get("dimension", dimension) == dimension]
                    decision = admit_candidate(
                        url=canonical,
                        offered_urls=offered,
                        competitor=task.input.get("competitor", ""),
                        dimension=dimension,
                        title=str(hits[0]["title"] if hits else ""),
                        excerpt=excerpt,
                        official_domains=task.input.get("official_domains", []),
                        official_repositories=task.input.get("official_repositories", []),
                    )
                    await self._investigations.record_control_event(
                        investigation_id,
                        user_id=user_id,
                        event_type="ci.evidence.admission",
                        stage=task.stage.value,
                        payload={"task_id": task.task_id, "url": canonical, "dimension": dimension, "accepted": decision.accepted, "reason": decision.reason, "source_tier": decision.source_tier},
                    )
                    if not decision.accepted or len(excerpt) < 20:
                        continue
                    stored = await self._investigations.get_candidate(investigation_id, hits[0].get("candidate_id", ""), user_id=user_id)
                    if stored is None or not stored["admitted"] or stored["competitor_id"] != task.input.get("competitor_id") or stored["url"] != canonical or stored["dimension"] != dimension:
                        raise InvestigationConflict("Evidence must reference an admitted owner-scoped candidate")
                    from app.investigations.providers import SourceDocument, SourceType

                    document = SourceDocument(url=stored["url"], content=stored["content"], source_type=SourceType(stored["source_type"]), extraction_method=stored["extraction_method"], mime_type=stored["mime_type"])
                    if document.extraction_method == "search_snippet":
                        await self._investigations.record_control_event(investigation_id, user_id=user_id, event_type="ci.evidence.rejected", stage=task.stage.value, payload={"url": canonical, "reason": "full_text_unavailable"})
                        continue
                    excerpt = locate_verbatim_quote(document.content, excerpt).quote
                    source_type = document.source_type
                    authority = {
                        "official": 30,
                        "pricing": 30,
                        "financial_report": 28,
                        "documentation": 27,
                        "github": 24,
                        "news": 20,
                    }.get(source_type.value, 15)
                    authority = 30 if decision.source_tier == "primary" else min(authority, 20)
                    result = await self._investigations.add_evidence(
                        investigation_id,
                        EvidenceCreate(
                            competitor_id=task.input.get("competitor_id"),
                            source_url=canonical,
                            canonical_url=canonical,
                            source_domain=canonical.split("/", 3)[2],
                            source_type=source_type,
                            title=str(hits[0]["title"] or canonical)[:500],
                            publisher=str(candidate.get("publisher"))[:255] if candidate.get("publisher") else None,
                            published_at=None,  # Model-proposed dates are not verified source metadata.
                            retrieved_at=datetime.now(UTC),
                            excerpt=excerpt[:20_000],
                            snapshot_text=document.content,
                            snapshot_mime_type=document.mime_type,
                            extraction_method=document.extraction_method,
                            content_hash=hashlib.sha256(document.content.encode("utf-8")).hexdigest(),
                            language=str(candidate.get("language") or "zh-CN")[:16],
                            source_authority=authority,
                            freshness=8,
                            extraction_quality=(2 if document.extraction_method == "search_snippet" else 8 if len(document.content) >= 500 else 5),
                            specificity=7,
                            corroboration=0,
                        ),
                        user_id=user_id,
                        agent_name=task.role,
                    )
                    if result is not None and result["id"] not in known_ids:
                        known_ids.add(result["id"])
                        accepted += 1
                        claim_id = task.input.get("claim_id")
                        if isinstance(claim_id, str):
                            await self._investigations.supplement_claim_evidence(
                                investigation_id,
                                claim_id,
                                [result["id"]],
                                user_id=user_id,
                                relation=ClaimEvidenceRelation(task.input.get("evidence_relation", "supports")),
                            )
                except (KeyError, ValueError, InvestigationConflict) as exc:
                    await self._investigations.record_control_event(investigation_id, user_id=user_id, event_type="ci.evidence.rejected", stage=task.stage.value, payload={"task_id": task.task_id, "reason": str(exc)[:500]})
                    continue
        return accepted

    async def _apply_claims(self, investigation_id: str, submissions: list[DomainSubmission], *, user_id: str, tasks: list[StageTask] | None = None, accepted_ids: list[str] | None = None) -> int:
        accepted = 0
        task_by_key = {task.item_key: task for task in tasks or []}
        for submission in submissions:
            if submission.kind is not SubmissionKind.CLAIMS:
                continue
            for index, candidate in enumerate(submission.payload.get("claims", [])[:20]):
                if not isinstance(candidate, dict):
                    continue
                try:
                    task = task_by_key.get(submission.item_key)
                    dimension = str(candidate.get("dimension") or "综合")
                    if task is not None and dimension not in task.input.get("dimensions", [dimension]):
                        raise InvestigationConflict("Claim dimension is outside its assigned research questions")
                    statement = AtomicStatement.model_validate(candidate.get("statement"))
                    if candidate.get("claim_type", "fact") not in {"fact", "pricing", "vendor_statement", "user_report"}:
                        raise InvestigationConflict("Research Claims must be facts, pricing or attributed source statements; hypotheses belong in report proposals")
                    if task and task.input.get("competitor") and statement.subject.casefold() != task.input["competitor"].casefold():
                        raise InvestigationConflict("Atomic subject must match the assigned competitor")
                    result = await self._investigations.add_claim(
                        investigation_id,
                        ClaimCreate(
                            competitor_id=task.input.get("competitor_id") if task else candidate.get("competitor_id"),
                            dimension=dimension,
                            text=statement.render(),
                            statement=statement,
                            material=True,  # Full text stays mandatory; source-aware policy decides sufficiency.
                            claim_type=str(candidate.get("claim_type") or "fact"),
                            evidence_bindings=candidate.get("evidence_bindings", []),
                            price_observations=candidate.get("price_observations", []),
                        ),
                        user_id=user_id,
                        agent_name=submission.item_key,
                        idempotency_key=f"{submission.task_id}:claim:{index}",
                    )
                    if result is not None:
                        accepted += 1
                        if accepted_ids is not None:
                            accepted_ids.append(result["id"])
                except (KeyError, ValueError, InvestigationConflict):
                    continue
        return accepted

    def _task(
        self,
        workflow: dict[str, Any],
        investigation: dict[str, Any],
        *,
        stage: StageName,
        item_key: str,
        role: str,
        input_data: dict[str, Any],
        acceptance: list[str],
    ) -> StageTask:
        idempotency_key = f"{workflow['id']}:{stage.value}:{item_key}"
        return StageTask(
            task_id=_stable_task_id(idempotency_key),
            investigation_id=investigation["id"],
            workflow_run_id=workflow["id"],
            stage=stage,
            item_key=item_key,
            role=role,
            idempotency_key=idempotency_key,
            input=input_data,
            acceptance_criteria=acceptance,
        )

    async def _require_investigation(self, investigation_id: str, user_id: str) -> dict[str, Any]:
        investigation = await self._investigations.get(investigation_id, user_id=user_id)
        if investigation is None:
            raise LookupError("Investigation not found")
        return investigation

    @staticmethod
    def _analysis_role(dimension: str) -> str:
        return {
            "功能": "product-analyst",
            "定价": "pricing-analyst",
            "定位": "market-analyst",
            "用户": "sentiment-analyst",
            "壁垒": "strategy-analyst",
        }.get(dimension, "competitive-analyst")
