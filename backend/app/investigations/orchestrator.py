from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from typing import Any

from app.investigations.batch_adapter import DurableStageBatchAdapter
from app.investigations.contracts import ClaimCreate, EvidenceCreate, InvestigationStatus, ResearchScope
from app.investigations.orchestration_repository import OrchestrationRepository
from app.investigations.protocols import DomainSubmission, StageName, StageTask, SubmissionKind
from app.investigations.providers import canonicalize_url
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
        owner_id: str,
    ) -> None:
        self._investigations = investigations
        self._orchestration = orchestration
        self._batches = batches
        self._runs = runs
        self._owner_id = owner_id

    async def execute(self, workflow: dict[str, Any], *, user_id: str) -> None:
        investigation_id = workflow["investigation_id"]
        investigation = await self._require_investigation(investigation_id, user_id)
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
            if investigation["status"] == InvestigationStatus.AUDITING.value:
                issues = await self._audit(workflow, investigation, user_id=user_id)
                if issues and investigation["rework_round"] < 2:
                    await self._investigations.begin_audit_rework(
                        investigation_id,
                        user_id=user_id,
                        issue_ids=[issue["id"] for issue in issues],
                    )
                    investigation = await self._require_investigation(investigation_id, user_id)
                    await self._rework(workflow, investigation, issues, user_id=user_id)
                    await self._investigations.transition(
                        investigation_id,
                        InvestigationStatus.ANALYZING,
                        user_id=user_id,
                        event_type="ci.stage.completed",
                        payload={"mode": "targeted_evidence_rework", "round": investigation["rework_round"]},
                    )
                    await self._investigations.transition(
                        investigation_id,
                        InvestigationStatus.AUDITING,
                        user_id=user_id,
                        event_type="ci.stage.started",
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
            ],
        )
        submission = await self._execute_run_task(
            workflow,
            task,
            user_id=user_id,
            instruction=("Act as the Research Director and Scope Mapper. Refine the user's proposed competitive-research scope. You may use search only to disambiguate product names. Return kind='scope' with payload.scope."),
        )
        if submission is None:
            return
        try:
            scope = ResearchScope.model_validate(submission.payload.get("scope"))
        except ValueError as exc:
            raise StageAcceptanceError(f"Planning returned an invalid research scope: {exc}") from exc
        await self._investigations.complete_planning(investigation["id"], scope, user_id=user_id, run_id=None)

    async def _collect(self, workflow: dict[str, Any], investigation: dict[str, Any], *, user_id: str) -> None:
        competitors = await self._investigations.list_competitors(investigation["id"], user_id=user_id) or []
        tasks = [
            self._task(
                workflow,
                investigation,
                stage=StageName.COLLECTING,
                item_key=f"{competitor['id']}:{dimension}",
                role="competitor-collector",
                input_data={
                    "brief": investigation["brief"],
                    "market": investigation["scope"]["market"],
                    "time_range": investigation["scope"]["time_range"],
                    "competitor_id": competitor["id"],
                    "competitor": competitor["name"],
                    "dimension": dimension,
                    "max_evidence": 5,
                },
                acceptance=[
                    "Return at least one evidence record with an absolute source URL.",
                    "Every excerpt must state a concrete fact, not a search-result title alone.",
                    "Do not invent publication dates, prices, or metrics.",
                ],
            )
            for competitor in competitors
            for dimension in investigation["scope"]["dimensions"][:6]
        ]
        attempt = await self._orchestration.start_stage(workflow["id"], owner_id=self._owner_id, stage=StageName.COLLECTING, tasks=tasks)
        if attempt["status"] == "completed":
            return
        batch = await self._batches.submit(
            stage_attempt_id=attempt["id"],
            tasks=tasks,
            user_id=user_id,
            model_name="deepseek-v4-flash",
            instruction=(
                "Act as a competitor evidence collector. Use web_search and web_fetch. Return kind='evidence' with "
                "payload.evidence as a list of objects containing url, title, excerpt, source_type, publisher, "
                "published_at when known, and language. Include only evidence you actually retrieved."
            ),
        )
        submissions, receipts = await self._batches.wait(batch_id=batch["id"], stage_attempt_id=attempt["id"], tasks=tasks, user_id=user_id)
        accepted = await self._apply_evidence(investigation["id"], submissions, tasks, user_id=user_id)
        successful_items = sum(receipt.status.value == "succeeded" for receipt in receipts)
        required_items = max(1, (len(tasks) * 3 + 4) // 5)
        stage_ok = successful_items >= required_items and accepted >= len(competitors) * 2
        await self._orchestration.finish_stage(
            attempt["id"],
            succeeded=stage_ok,
            error=None if stage_ok else f"Collection coverage failed: items={successful_items}/{len(tasks)}, evidence={accepted}",
        )
        if not stage_ok:
            raise StageAcceptanceError("Collection did not reach the 60% item and minimum evidence thresholds")

    async def _analyze(self, workflow: dict[str, Any], investigation: dict[str, Any], *, user_id: str) -> None:
        evidence = await self._investigations.list_evidence(investigation["id"], user_id=user_id) or []
        compact = [
            {
                "id": item["id"],
                "domain": item["source_domain"],
                "title": item["title"],
                "excerpt": item["excerpt"][:1600],
                "credibility_score": item["credibility_score"],
            }
            for item in evidence[:100]
        ]
        tasks = [
            self._task(
                workflow,
                investigation,
                stage=StageName.ANALYZING,
                item_key=f"dimension:{dimension}",
                role=self._analysis_role(dimension),
                input_data={
                    "brief": investigation["brief"],
                    "competitors": investigation["scope"]["competitors"],
                    "dimension": dimension,
                    "evidence": compact,
                },
                acceptance=[
                    "Every factual claim references only provided evidence IDs.",
                    "Material claims should cite two independent domains when evidence permits.",
                    "Missing support must remain an explicit uncertainty.",
                ],
            )
            for dimension in investigation["scope"]["dimensions"]
        ]
        attempt = await self._orchestration.start_stage(workflow["id"], owner_id=self._owner_id, stage=StageName.ANALYZING, tasks=tasks)
        if attempt["status"] == "completed":
            return
        batch = await self._batches.submit(
            stage_attempt_id=attempt["id"],
            tasks=tasks,
            user_id=user_id,
            model_name="deepseek-v4-flash",
            instruction=(
                "Act as the named competitive analyst. Return kind='claims' with payload.claims. Each claim contains "
                "dimension, text, material, claim_type='fact', and evidence_ids. Do not repeat evidence text as a "
                "claim and do not use evidence IDs outside the task envelope."
            ),
        )
        submissions, receipts = await self._batches.wait(batch_id=batch["id"], stage_attempt_id=attempt["id"], tasks=tasks, user_id=user_id)
        accepted = await self._apply_claims(investigation["id"], submissions, user_id=user_id)
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

    async def _audit(self, workflow: dict[str, Any], investigation: dict[str, Any], *, user_id: str) -> list[dict[str, Any]]:
        evidence = await self._investigations.list_evidence(investigation["id"], user_id=user_id) or []
        claims = await self._investigations.list_claims(investigation["id"], user_id=user_id) or []
        deterministic = self._deterministic_audit_issues(claims, evidence)
        task = self._task(
            workflow,
            investigation,
            stage=StageName.AUDITING,
            item_key=f"evidence-audit:round-{investigation['rework_round']}",
            role="evidence-auditor",
            input_data={
                "claims": claims[:200],
                "evidence": [
                    {
                        "id": item["id"],
                        "domain": item["source_domain"],
                        "source_type": item["source_type"],
                        "title": item["title"],
                        "excerpt": item["excerpt"][:1200],
                    }
                    for item in evidence[:200]
                ],
                "deterministic_issues": deterministic,
            },
            acceptance=[
                "Never waive a deterministic issue unless the cited evidence directly disproves it.",
                "Check whether each excerpt actually entails its claim and flag conflicts or unsupported exact values.",
                "Return payload.issues with claim_id, severity, rule, reason, and required_action.",
            ],
        )
        submission = await self._execute_run_task(
            workflow,
            task,
            user_id=user_id,
            instruction=(
                "Act as an independent Evidence Auditor. Return kind='audit'. Treat deterministic_issues as mandatory. "
                "Add semantic-support, contradiction, exact-number, pricing-source, and source-independence issues that "
                "you can justify from the supplied records. Return payload.issues as a JSON list."
            ),
        )
        if submission is None:
            return await self._investigations.list_audit_issues(investigation["id"], user_id=user_id, status="open") or []
        issues = list(deterministic)
        known_claim_ids = {claim["id"] for claim in claims}
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
        return persisted or []

    async def _rework(
        self,
        workflow: dict[str, Any],
        investigation: dict[str, Any],
        issues: list[dict[str, Any]],
        *,
        user_id: str,
    ) -> None:
        claims = await self._investigations.list_claims(investigation["id"], user_id=user_id) or []
        claim_by_id = {claim["id"]: claim for claim in claims}
        tasks = [
            self._task(
                workflow,
                investigation,
                stage=StageName.REWORKING,
                item_key=f"round-{investigation['rework_round']}:{issue['id']}",
                role="competitor-collector",
                input_data={
                    "brief": investigation["brief"],
                    "claim_id": issue.get("claim_id"),
                    "claim": claim_by_id.get(issue.get("claim_id")),
                    "audit_rule": issue["rule"],
                    "audit_reason": issue["reason"],
                    "required_action": issue["required_action"],
                    "max_evidence": 3,
                },
                acceptance=[
                    "Collect evidence targeted to this exact audit issue.",
                    "Prefer an independent primary domain not already cited by the claim.",
                    "Return an empty evidence list when no reliable source is found.",
                ],
            )
            for issue in issues[:20]
            if issue.get("claim_id") in claim_by_id
        ]
        if not tasks:
            return
        attempt = await self._orchestration.start_stage(workflow["id"], owner_id=self._owner_id, stage=StageName.REWORKING, tasks=tasks)
        if attempt["status"] == "completed":
            return
        batch = await self._batches.submit(
            stage_attempt_id=attempt["id"],
            tasks=tasks,
            user_id=user_id,
            model_name="deepseek-v4-flash",
            instruction=("Act as a targeted evidence collector. Use web_search and web_fetch. Return kind='evidence' with payload.evidence containing only sources that address the stated audit issue."),
        )
        submissions, receipts = await self._batches.wait(batch_id=batch["id"], stage_attempt_id=attempt["id"], tasks=tasks, user_id=user_id)
        accepted = await self._apply_evidence(investigation["id"], submissions, tasks, user_id=user_id)
        succeeded = sum(receipt.status.value == "succeeded" for receipt in receipts)
        stage_ok = succeeded > 0
        await self._orchestration.finish_stage(
            attempt["id"],
            succeeded=stage_ok,
            error=None if stage_ok else f"All targeted rework items failed; accepted_evidence={accepted}",
        )
        if not stage_ok:
            raise StageAcceptanceError("All targeted evidence rework items failed")

    async def _synthesize(self, workflow: dict[str, Any], investigation: dict[str, Any], *, user_id: str) -> None:
        claims = await self._investigations.list_claims(investigation["id"], user_id=user_id) or []
        evidence = await self._investigations.list_evidence(investigation["id"], user_id=user_id) or []
        issues = await self._investigations.list_audit_issues(investigation["id"], user_id=user_id, status="open") or []
        task = self._task(
            workflow,
            investigation,
            stage=StageName.SYNTHESIZING,
            item_key=f"report:round-{investigation['rework_round']}",
            role="report-editor",
            input_data={
                "title": investigation["title"],
                "scope": investigation["scope"],
                "claims": claims[:250],
                "evidence": [{"id": item["id"], "title": item["title"], "url": item["source_url"], "excerpt": item["excerpt"][:800]} for item in evidence[:250]],
                "open_audit_issues": issues,
                "required_section_types": list(self._required_report_sections()),
            },
            acceptance=[
                "Return all required section types exactly once.",
                "Every factual paragraph lists claim_ids and evidence_ids from the task envelope.",
                "Keep uncertain claims and unresolved audit issues explicit; never upgrade them to facts.",
            ],
        )
        submission = await self._execute_run_task(
            workflow,
            task,
            user_id=user_id,
            instruction=("Act as the Report Editor. Return kind='report' with payload.sections. Each section is an object with id, type, title, markdown, claim_ids, and evidence_ids. Produce the report in the requested language."),
        )
        if submission is None:
            return
        structured, markdown = self._validate_report_submission(investigation, submission, claims, evidence)
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
            submission, receipt = await self._runs.execute(
                task,
                user_id=user_id,
                instruction=instruction,
                existing_run_id=attempt.get("run_id"),
                on_run_started=lambda run_id: self._orchestration.bind_run(attempt["id"], run_id=run_id),
            )
            receipt.attempt = attempt["attempt"]
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
            if attempt["attempt"] >= 3:
                raise StageAcceptanceError(receipt.error or f"{task.stage.value} run did not return an accepted submission")

    @staticmethod
    def _deterministic_audit_issues(claims: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
        evidence_by_id = {item["id"]: item for item in evidence}
        issues: list[dict[str, Any]] = []
        for claim in claims:
            cited = [evidence_by_id[value] for value in claim["evidence_ids"] if value in evidence_by_id]
            domains = {item["source_domain"].lower() for item in cited}
            required = 2 if claim["material"] else 1
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
            elif len(domains) < required:
                issues.append(
                    {
                        "claim_id": claim["id"],
                        "severity": "warning",
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

    def _validate_report_submission(
        self,
        investigation: dict[str, Any],
        submission: DomainSubmission,
        claims: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], str]:
        raw_sections = submission.payload.get("sections")
        if not isinstance(raw_sections, list):
            raise StageAcceptanceError("Report submission is missing payload.sections")
        claim_ids = {item["id"] for item in claims}
        evidence_ids = {item["id"] for item in evidence}
        sections: list[dict[str, Any]] = []
        seen: set[str] = set()
        for position, raw in enumerate(raw_sections):
            if not isinstance(raw, dict):
                continue
            section_type = str(raw.get("type") or "")
            if section_type not in self._required_report_sections() or section_type in seen:
                continue
            section_claims = [str(value) for value in raw.get("claim_ids", []) if str(value) in claim_ids]
            section_evidence = [str(value) for value in raw.get("evidence_ids", []) if str(value) in evidence_ids]
            sections.append(
                {
                    "id": str(raw.get("id") or f"section-{position + 1}"),
                    "type": section_type,
                    "title": str(raw.get("title") or section_type.replace("_", " ").title()),
                    "markdown": str(raw.get("markdown") or "").strip(),
                    "claim_ids": section_claims,
                    "evidence_ids": section_evidence,
                }
            )
            seen.add(section_type)
        missing = set(self._required_report_sections()) - seen
        if missing:
            raise StageAcceptanceError("Report is missing required sections: " + ", ".join(sorted(missing)))
        markdown = "\n\n".join(f"## {section['title']}\n\n{section['markdown']}" for section in sections)
        return {
            "schema_version": "competitive-report-v1",
            "title": investigation["title"],
            "sections": sections,
        }, markdown

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
        for submission in submissions:
            if submission.kind is not SubmissionKind.EVIDENCE:
                continue
            task = task_by_key[submission.item_key]
            for candidate in submission.payload.get("evidence", [])[:5]:
                if not isinstance(candidate, dict):
                    continue
                try:
                    canonical = canonicalize_url(str(candidate["url"]))
                    excerpt = str(candidate.get("excerpt") or "").strip()
                    if len(excerpt) < 40:
                        continue
                    source_type = str(candidate.get("source_type") or "web")[:32]
                    authority = {"official": 30, "financial_report": 28, "documentation": 27, "news": 20}.get(source_type, 15)
                    result = await self._investigations.add_evidence(
                        investigation_id,
                        EvidenceCreate(
                            competitor_id=task.input.get("competitor_id"),
                            source_url=canonical,
                            canonical_url=canonical,
                            source_domain=canonical.split("/", 3)[2],
                            source_type=source_type,
                            title=str(candidate.get("title") or canonical)[:500],
                            publisher=str(candidate.get("publisher"))[:255] if candidate.get("publisher") else None,
                            published_at=candidate.get("published_at"),
                            retrieved_at=datetime.now(UTC),
                            excerpt=excerpt[:20_000],
                            content_hash=hashlib.sha256(excerpt.encode("utf-8")).hexdigest(),
                            language=str(candidate.get("language") or "zh-CN")[:16],
                            source_authority=authority,
                            freshness=15 if candidate.get("published_at") else 8,
                            extraction_quality=8 if len(excerpt) >= 500 else 5,
                            specificity=7,
                            corroboration=0,
                        ),
                        user_id=user_id,
                        agent_name=task.role,
                    )
                    if result is not None:
                        accepted += 1
                        claim_id = task.input.get("claim_id")
                        if isinstance(claim_id, str):
                            await self._investigations.supplement_claim_evidence(
                                investigation_id,
                                claim_id,
                                [result["id"]],
                                user_id=user_id,
                            )
                except (KeyError, ValueError, InvestigationConflict):
                    continue
        return accepted

    async def _apply_claims(self, investigation_id: str, submissions: list[DomainSubmission], *, user_id: str) -> int:
        accepted = 0
        for submission in submissions:
            if submission.kind is not SubmissionKind.CLAIMS:
                continue
            for index, candidate in enumerate(submission.payload.get("claims", [])[:20]):
                if not isinstance(candidate, dict):
                    continue
                try:
                    result = await self._investigations.add_claim(
                        investigation_id,
                        ClaimCreate(
                            dimension=str(candidate.get("dimension") or "综合"),
                            text=str(candidate["text"]),
                            material=bool(candidate.get("material", True)),
                            claim_type=str(candidate.get("claim_type") or "fact"),
                            evidence_ids=[str(value) for value in candidate.get("evidence_ids", [])],
                        ),
                        user_id=user_id,
                        agent_name=submission.item_key,
                        idempotency_key=f"{submission.task_id}:claim:{index}",
                    )
                    if result is not None:
                        accepted += 1
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
