from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import socket
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi.encoders import jsonable_encoder

from app.investigations.contracts import ClaimCreate, EvidenceCreate, InvestigationStatus, ResearchScope
from app.investigations.orchestration_repository import OrchestrationRepository, WorkflowLeaseLost
from app.investigations.orchestrator import DurableCompetitiveOrchestrator
from app.investigations.providers import ResearchProviderRegistry, canonicalize_url
from app.investigations.repository import InvestigationConflict, InvestigationRepository
from deerflow.config import get_app_config
from deerflow.utils.oneshot_llm import run_oneshot_llm

logger = logging.getLogger(__name__)


class InvestigationWorkflowService:
    """Process-local worker for V1; durable state makes startup recovery safe."""

    def __init__(
        self,
        repository: InvestigationRepository,
        providers: ResearchProviderRegistry | None = None,
        *,
        orchestration_repository: OrchestrationRepository | None = None,
        durable_orchestrator: DurableCompetitiveOrchestrator | None = None,
        owner_id: str | None = None,
    ) -> None:
        self._repo = repository
        self._providers = providers or ResearchProviderRegistry()
        self._providers.validate_startup()
        self._orchestration = orchestration_repository
        self._durable_orchestrator = durable_orchestrator
        self._owner_id = owner_id or f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
        self._lease_seconds = 120
        self._queue: asyncio.Queue[tuple[str, str]] = asyncio.Queue(maxsize=100)
        self._active: set[str] = set()
        self._worker: asyncio.Task[None] | None = None

    @property
    def provider_status(self) -> dict[str, bool]:
        return {
            **self._providers.status(),
            "durable_orchestration": self._durable_orchestrator is not None,
        }

    async def start(self) -> None:
        if self._worker is None:
            self._worker = asyncio.create_task(self._run_loop(), name="competitive-research-worker")
            if self._orchestration is not None:
                for workflow in await self._orchestration.recoverable_workflows():
                    self.enqueue(workflow["investigation_id"], workflow["user_id"])
                for investigation_id, user_id in await self._repo.list_recoverable():
                    self.enqueue(investigation_id, user_id)
            else:
                for investigation_id, user_id in await self._repo.list_recoverable():
                    self.enqueue(investigation_id, user_id)

    async def stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            await asyncio.gather(self._worker, return_exceptions=True)
            self._worker = None

    def enqueue(self, investigation_id: str, user_id: str) -> bool:
        if investigation_id in self._active:
            return False
        self._active.add(investigation_id)
        try:
            self._queue.put_nowait((investigation_id, user_id))
        except asyncio.QueueFull:
            self._active.discard(investigation_id)
            raise RuntimeError("Competitive Research queue is full") from None
        return True

    async def _run_loop(self) -> None:
        while True:
            investigation_id, user_id = await self._queue.get()
            try:
                if self._orchestration is not None and self._durable_orchestrator is not None:
                    investigation = await self._repo.get(investigation_id, user_id=user_id)
                    if investigation is None:
                        continue
                    workflow = await self._orchestration.ensure_workflow(
                        investigation_id,
                        user_id=user_id,
                        idempotency_key=(
                            f"{investigation_id}:planning"
                            if investigation["status"] == InvestigationStatus.PLANNING.value
                            else (f"{investigation_id}:execution:round:{investigation['rework_round']}:recovery:{investigation['failure_retry_count']}:annotation:{investigation.get('active_request_id') or 'none'}")
                        ),
                    )
                    claimed = await self._orchestration.claim_workflow(
                        workflow["id"],
                        owner_id=self._owner_id,
                        lease_seconds=self._lease_seconds,
                    )
                    if claimed is None:
                        asyncio.get_running_loop().call_later(
                            self._lease_seconds / 3,
                            self.enqueue,
                            investigation_id,
                            user_id,
                        )
                        continue
                    heartbeat = asyncio.create_task(self._heartbeat(workflow["id"]), name=f"ci-heartbeat-{workflow['id']}")
                    execution = asyncio.create_task(
                        self._durable_orchestrator.execute(claimed, user_id=user_id),
                        name=f"ci-workflow-{workflow['id']}",
                    )
                    try:
                        done, _ = await asyncio.wait({execution, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
                        if heartbeat in done:
                            execution.cancel()
                            await asyncio.gather(execution, return_exceptions=True)
                            heartbeat.result()
                            raise WorkflowLeaseLost("Competitive Research heartbeat stopped unexpectedly")
                        await execution
                        finalized = await self._orchestration.finalize_workflow(workflow["id"], owner_id=self._owner_id, succeeded=True)
                        if not finalized:
                            raise WorkflowLeaseLost("Workflow completed after its lease was lost")
                    except Exception:
                        await self._orchestration.finalize_workflow(workflow["id"], owner_id=self._owner_id, succeeded=False)
                        raise
                    finally:
                        if not execution.done():
                            execution.cancel()
                            await asyncio.gather(execution, return_exceptions=True)
                        heartbeat.cancel()
                        await asyncio.gather(heartbeat, return_exceptions=True)
                else:
                    raise RuntimeError("Competitive Research requires durable subagent batches; enable subagent_batches.enabled")
            except asyncio.CancelledError:
                raise
            except WorkflowLeaseLost:
                logger.warning("Competitive Research workflow lease lost; a recovery worker may continue: %s", investigation_id)
            except Exception as exc:
                logger.exception("Competitive Research workflow failed: %s", investigation_id)
                try:
                    await self._repo.transition(investigation_id, InvestigationStatus.FAILED, user_id=user_id, event_type="ci.failed", payload={"message": str(exc)[:1000]})
                except Exception:
                    logger.exception("Could not persist investigation failure: %s", investigation_id)
            finally:
                self._active.discard(investigation_id)
                self._queue.task_done()

    async def _heartbeat(self, workflow_run_id: str) -> None:
        while True:
            await asyncio.sleep(self._lease_seconds / 3)
            renewed = await self._orchestration.renew_workflow_lease(
                workflow_run_id,
                owner_id=self._owner_id,
                lease_seconds=self._lease_seconds,
            )
            if not renewed:
                raise WorkflowLeaseLost("Competitive Research workflow lease was lost")

    async def _execute(self, investigation_id: str, user_id: str) -> None:
        investigation = await self._repo.get(investigation_id, user_id=user_id)
        if investigation is None:
            return
        if investigation["status"] == InvestigationStatus.PLANNING.value:
            await self._repo.complete_planning(
                investigation_id,
                ResearchScope.model_validate(investigation["scope"]),
                user_id=user_id,
                run_id=None,
            )
            return
        if investigation["status"] == InvestigationStatus.COLLECTING.value:
            competitors = await self._repo.list_competitors(investigation_id, user_id=user_id) or []
            seen = {item["canonical_url"] for item in (await self._repo.list_evidence(investigation_id, user_id=user_id) or [])}
            for competitor in competitors:
                accepted = 0
                for dimension in investigation["scope"]["dimensions"][:6]:
                    hits = await self._providers.search(f"{competitor['name']} {dimension} 产品 竞品 2026", max_results=5)
                    for hit in hits:
                        if accepted >= 30:
                            break
                        canonical = canonicalize_url(hit.url)
                        if canonical in seen:
                            continue
                        seen.add(canonical)
                        document = await self._providers.fetch_url(hit.url, fallback_content=hit.content, investigation_id=investigation_id)
                        content = document.content.strip()
                        if len(content) < 40:
                            continue
                        try:
                            await self._repo.add_evidence(
                                investigation_id,
                                EvidenceCreate(
                                    competitor_id=competitor["id"],
                                    source_url=hit.url,
                                    canonical_url=canonical,
                                    source_domain=canonical.split("/", 3)[2],
                                    source_type=document.source_type,
                                    title=hit.title,
                                    retrieved_at=datetime.now(UTC),
                                    excerpt=content[:20_000],
                                    snapshot_text=content,
                                    snapshot_mime_type=document.mime_type,
                                    extraction_method=document.extraction_method,
                                    content_hash=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                                    language=investigation["scope"]["language"],
                                    source_authority=20,
                                    freshness=15,
                                    extraction_quality=8 if len(content) > 500 else 5,
                                    specificity=7,
                                    corroboration=0,
                                ),
                                user_id=user_id,
                                agent_name="competitor-collector",
                            )
                            accepted += 1
                        except InvestigationConflict:
                            continue
            await self._repo.transition(investigation_id, InvestigationStatus.NORMALIZING, user_id=user_id, event_type="ci.stage.started")

        investigation = await self._repo.get(investigation_id, user_id=user_id)
        assert investigation is not None
        if investigation["status"] == InvestigationStatus.NORMALIZING.value:
            await self._repo.transition(investigation_id, InvestigationStatus.ANALYZING, user_id=user_id, event_type="ci.stage.started")

        evidence = await self._repo.list_evidence(investigation_id, user_id=user_id) or []
        if not evidence:
            raise RuntimeError("No usable evidence was collected")

        investigation = await self._repo.get(investigation_id, user_id=user_id)
        assert investigation is not None
        if investigation["status"] == InvestigationStatus.ANALYZING.value:
            existing_claims = await self._repo.list_claims(investigation_id, user_id=user_id) or []
            if not existing_claims:
                for claim in await self._analyze(investigation, evidence):
                    await self._repo.add_claim(investigation_id, ClaimCreate(**claim), user_id=user_id, agent_name="competitive-analyst")
            await self._repo.transition(investigation_id, InvestigationStatus.AUDITING, user_id=user_id, event_type="ci.stage.started")

        persisted_claims = await self._repo.list_claims(investigation_id, user_id=user_id) or []
        investigation = await self._repo.get(investigation_id, user_id=user_id)
        assert investigation is not None
        if investigation["status"] == InvestigationStatus.AUDITING.value:
            await self._repo.transition(
                investigation_id,
                InvestigationStatus.SYNTHESIZING,
                user_id=user_id,
                event_type="ci.stage.started",
                payload={"supported": sum(c["status"] == "supported" for c in persisted_claims), "uncertain": sum(c["status"] == "uncertain" for c in persisted_claims)},
            )
        investigation = await self._repo.get(investigation_id, user_id=user_id)
        assert investigation is not None
        if investigation["status"] == InvestigationStatus.SYNTHESIZING.value:
            structured_report, markdown = self._build_report(investigation, persisted_claims, evidence)
            await self._repo.create_report(
                investigation_id,
                structured_data=jsonable_encoder(structured_report),
                rendered_markdown=markdown,
                user_id=user_id,
            )

    async def _analyze(self, investigation: dict[str, Any], evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
        package = [
            {
                "id": item["id"],
                "domain": item["source_domain"],
                "title": item["title"],
                "excerpt": item["excerpt"][:1800],
                "content_hash": item["content_hash"],
            }
            for item in evidence[:80]
        ]
        output = await run_oneshot_llm(
            system_instruction=(
                "You are a competitive intelligence analyst. Return JSON only: an array of factual claims. "
                "Each item has dimension, text, material, claim_type, evidence_bindings, and price_observations. "
                "Each evidence binding contains evidence_id, relation, verbatim_quote copied exactly from the "
                "provided excerpt, and snapshot_sha256 copied from content_hash. Pricing claims require structured "
                "observations. Never cite an Evidence ID that was not provided."
            ),
            user_content=json.dumps(jsonable_encoder({"brief": investigation["brief"], "scope": investigation["scope"], "evidence": package}), ensure_ascii=False),
            run_name="competitive-research-analysis",
            app_config=get_app_config(),
            model_name="deepseek-v4-pro",
            thread_id=investigation["id"],
        )
        text = output.strip().removeprefix("```json").removesuffix("```").strip()
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            raise ValueError("Competitive analysis model did not return a claim array")
        allowed = {item["id"] for item in evidence}
        claims = []
        for item in parsed[:40]:
            if not isinstance(item, dict):
                continue
            bindings = [binding for binding in item.get("evidence_bindings", []) if isinstance(binding, dict) and binding.get("evidence_id") in allowed]
            claims.append(
                {
                    "dimension": str(item.get("dimension") or "综合"),
                    "text": str(item.get("text") or ""),
                    "material": bool(item.get("material", True)),
                    "claim_type": str(item.get("claim_type") or "fact"),
                    "evidence_bindings": bindings,
                    "price_observations": item.get("price_observations", []),
                }
            )
        return [claim for claim in claims if claim["text"]]

    @staticmethod
    def _build_report(investigation: dict[str, Any], claims: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> tuple[dict[str, Any], str]:
        definitions = [
            ("executive_summary", "Executive Summary", None),
            ("methodology", "研究范围与方法", None),
            ("landscape", "赛道和竞品定义", "定位"),
            ("profiles", "竞品画像", "用户"),
            ("features", "功能能力矩阵", "功能"),
            ("pricing", "定价与商业模式", "定价"),
            ("positioning", "用户与市场定位", "定位"),
            ("moats", "优势、短板和竞争壁垒", "壁垒"),
            ("opportunities", "差异化机会", None),
            ("risks", "风险、冲突证据和未知项", None),
        ]
        sections: list[dict[str, Any]] = []
        all_claim_ids = [claim["id"] for claim in claims]
        for section_type, title, dimension in definitions:
            selected = [claim for claim in claims if dimension is None or claim["dimension"] == dimension]
            if section_type == "risks":
                selected = [claim for claim in claims if claim["status"] != "supported"]
            section_lines = [f"## {title}", ""]
            if section_type == "methodology":
                section_lines.append(f"研究市场：{investigation['scope']['market']}；时间范围：{investigation['scope']['time_range']}；竞品：{'、'.join(investigation['scope']['competitors'])}。")
            elif not selected:
                section_lines.append("当前证据不足，未形成可审计结论。")
            else:
                for claim in selected[:12]:
                    marker = "已验证" if claim["status"] == "supported" else "证据不足"
                    refs = ", ".join(f"[{ref}]" for ref in claim["evidence_ids"])
                    section_lines.append(f"- **{marker} · {claim['dimension']}**：{claim['text']} {refs}")
            evidence_ids = sorted({evidence_id for claim in selected for evidence_id in claim["evidence_ids"]})
            sections.append({"id": section_type, "type": section_type, "title": title, "claim_ids": [claim["id"] for claim in selected], "evidence_ids": evidence_ids, "markdown": "\n".join(section_lines)})
        appendix_lines = ["## Evidence Appendix", ""]
        for item in evidence:
            appendix_lines.append(f'- <a id="{item["id"]}"></a> **{item["title"]}** — {item["source_domain"]}，可信度 {item["credibility_score"]}/100。{item["source_url"]}')
        sections.append({"id": "evidence_appendix", "type": "evidence_appendix", "title": "Evidence Appendix", "claim_ids": all_claim_ids, "evidence_ids": [item["id"] for item in evidence], "markdown": "\n".join(appendix_lines)})
        markdown = f"# {investigation['title']}\n\n" + "\n\n".join(section["markdown"] for section in sections) + "\n"
        return {"schema_version": "1", "investigation_id": investigation["id"], "sections": sections}, markdown
