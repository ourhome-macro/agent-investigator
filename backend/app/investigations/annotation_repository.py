"""Owner-scoped, immutable report feedback and bounded follow-up admission."""

from __future__ import annotations

import hashlib
import re
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update

from app.investigations.confidence import claim_display_text
from app.investigations.contracts import AnnotationRequest
from app.investigations.evidence_validation import locate_verbatim_quote
from app.investigations.persistence.models import AuditIssueRow, ClaimRow, CompetitorRow, InvestigationEventRow, InvestigationRow, ReportRow, ReportSectionRow, ResearchRequestRow, ScopeRow
from app.investigations.product import policy_for


def require_report_selection(markdown: str, selected_text: str) -> None:
    """Match the visible text of our server-rendered sections, not source evidence.

    Browser selections omit generated list, emphasis and table delimiters. Keep
    the transformation limited to that report grammar; Evidence quotes continue
    to use the immutable-snapshot matcher without any markup removal.
    """
    lines = []
    for line in markdown.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [cell.strip().replace("\\|", "|") for cell in re.split(r"(?<!\\)\|", stripped[1:-1])]
            if cells and all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells):
                continue
            line = " ".join(cells)
        else:
            line = re.sub(r"^\s*- ", "", line)
        lines.append(line.replace("**", ""))
    try:
        locate_verbatim_quote("\n".join(lines), selected_text)
    except ValueError as exc:
        raise ValueError("Selected text does not match this report; select a continuous passage again") from exc


class AnnotationRepository:
    def __init__(self, session_factory):
        self._sf = session_factory

    @staticmethod
    def view(row):
        return {
            "id": row.id,
            "source_report_id": row.source_report_id,
            "result_report_id": row.result_report_id,
            "status": row.status,
            "payload": row.payload,
            "target": row.target,
            "token_allowance": row.token_allowance,
            "created_at": row.created_at,
            "error": row.error,
        }

    async def create(self, investigation_id: str, body: AnnotationRequest, *, user_id: str):
        key = hashlib.sha256(f"{investigation_id}:{user_id}:{body.idempotency_key}".encode()).hexdigest()
        payload = body.model_dump(mode="json")
        async with self._sf() as session, session.begin():
            inv = await session.get(InvestigationRow, investigation_id, with_for_update=True)
            if inv is None or inv.user_id != user_id:
                return None
            existing = await session.get(ResearchRequestRow, key)
            if existing:
                if existing.payload != payload:
                    raise ValueError("This feedback key was already used for a different request")
                return self.view(existing)
            if inv.status not in {"awaiting_publish_approval", "published"} or inv.active_request_id:
                raise ValueError("Another research run is active; wait for it before requesting follow-up")
            if inv.token_reserved:
                raise ValueError("Previous execution budget is still reserved; settle it before follow-up")
            report = (await session.execute(select(ReportRow).where(ReportRow.investigation_id == investigation_id).order_by(ReportRow.version.desc()).limit(1))).scalar_one_or_none()
            if report is None or report.version != body.report_version:
                raise ValueError("Report version changed; reload before annotating")
            section = next((item for item in report.structured_data.get("sections", []) if item.get("type") == body.section_key), None)
            if section is None:
                raise ValueError("Selected report section does not exist")
            if body.claim_id:
                claim = await session.get(ClaimRow, body.claim_id)
                expected = report.structured_data.get("claim_versions", {}).get(body.claim_id)
                if (
                    claim is None
                    or claim.investigation_id != investigation_id
                    or claim.version != body.claim_version
                    or expected != body.claim_version
                    or claim.id not in section.get("claim_ids", [])
                    or claim.status in {"superseded", "rejected"}
                ):
                    raise ValueError("Claim or report version changed; choose a current report finding")
                if body.selected_text:
                    try:
                        locate_verbatim_quote(claim_display_text({"text": claim.text, "support_basis": claim.support_basis}), body.selected_text)
                    except ValueError:
                        require_report_selection(section.get("markdown", ""), body.selected_text)
                target = {"claim_id": claim.id, "claim_version": claim.version, "competitor_id": claim.competitor_id, "dimension": claim.dimension}
            else:
                competitor = await session.get(CompetitorRow, body.competitor_id)
                scope = (await session.execute(select(ScopeRow).where(ScopeRow.investigation_id == investigation_id))).scalar_one()
                cell = next((cell for cell in report.structured_data.get("coverage", []) if cell["competitor_id"] == body.competitor_id and cell["dimension"] == body.dimension), None)
                if (
                    competitor is None
                    or competitor.investigation_id != investigation_id
                    or body.dimension not in scope.dimensions
                    or cell is None
                    or cell["status"] == "covered"
                    or body.section_key not in {"feature_matrix", "risks_unknowns"}
                ):
                    raise ValueError("Choose an unresolved question from this report's coverage matrix")
                if body.selected_text:
                    require_report_selection(section.get("markdown", ""), body.selected_text)
                target = {"claim_id": None, "claim_version": None, "competitor_id": competitor.id, "dimension": body.dimension}
            if not target["competitor_id"]:
                raise ValueError("This legacy finding has no competitor; regenerate it before targeted research")
            policy = policy_for({"policy_snapshot": inv.policy_snapshot})
            count = (await session.execute(select(func.count()).select_from(ResearchRequestRow).where(ResearchRequestRow.investigation_id == investigation_id))).scalar_one()
            if count >= policy["max_annotations"]:
                raise ValueError("Follow-up limit reached; start a new investigation for further research")
            now = datetime.now(UTC)
            changed = await session.execute(
                update(InvestigationRow)
                .where(InvestigationRow.id == investigation_id, InvestigationRow.status.in_(["awaiting_publish_approval", "published"]), InvestigationRow.active_request_id.is_(None), InvestigationRow.token_reserved == 0)
                .values(
                    status="reworking",
                    active_request_id=key,
                    token_budget=InvestigationRow.token_used + policy["refinement_tokens"],
                    deadline_at=now + timedelta(minutes=policy["refinement_minutes"]),
                    rework_round=0,
                    failure_retry_count=0,
                    error=None,
                    updated_at=now,
                )
            )
            if changed.rowcount != 1:
                existing = await session.get(ResearchRequestRow, key)
                if existing and existing.payload == payload:
                    return self.view(existing)
                raise ValueError("Research state changed; reload before requesting follow-up")
            request = ResearchRequestRow(id=key, investigation_id=investigation_id, source_report_id=report.id, payload=payload, target=target, token_allowance=policy["refinement_tokens"], status="queued")
            session.add(request)
            section_row = (await session.execute(select(ReportSectionRow).where(ReportSectionRow.report_id == report.id, ReportSectionRow.section_type == body.section_key))).scalar_one_or_none()
            reason = body.comment + (f"\n报告选段：{body.selected_text}" if body.selected_text else "")
            session.add(
                AuditIssueRow(
                    id=uuid.uuid4().hex,
                    investigation_id=investigation_id,
                    claim_id=target["claim_id"],
                    section_id=section_row.id if section_row else None,
                    severity="error",
                    rule="user_annotation",
                    reason=reason,
                    required_action=body.action,
                    status="open",
                    raised_by=f"annotation:{key}",
                )
            )
            session.add(
                InvestigationEventRow(
                    investigation_id=investigation_id,
                    stage="reworking",
                    event_type="ci.annotation.requested",
                    payload={"request_id": key, "source_report_version": report.version, "target": target, "token_allowance": policy["refinement_tokens"]},
                )
            )
        return self.view(request)

    async def list(self, investigation_id: str, *, user_id: str):
        async with self._sf() as session:
            inv = await session.get(InvestigationRow, investigation_id)
            if inv is None or inv.user_id != user_id:
                return None
            rows = (await session.execute(select(ResearchRequestRow).where(ResearchRequestRow.investigation_id == investigation_id).order_by(ResearchRequestRow.created_at))).scalars()
            return [self.view(row) for row in rows]

    async def active(self, investigation_id: str, *, user_id: str):
        async with self._sf() as session:
            inv = await session.get(InvestigationRow, investigation_id)
            if inv is None or inv.user_id != user_id or not inv.active_request_id:
                return None
            row = await session.get(ResearchRequestRow, inv.active_request_id)
            return self.view(row) if row else None

    async def mark(self, investigation_id: str, *, user_id: str, status: str, error: str | None = None):
        async with self._sf() as session, session.begin():
            inv = await session.get(InvestigationRow, investigation_id, with_for_update=True)
            if inv is None or inv.user_id != user_id or not inv.active_request_id:
                return
            row = await session.get(ResearchRequestRow, inv.active_request_id)
            row.status, row.error = ("cancelled" if inv.status in {"cancelled", "cancelling"} else status), error

    async def resolve_gap(self, investigation_id: str, *, user_id: str, request_id: str, covered: bool):
        if not covered:
            return
        async with self._sf() as session, session.begin():
            inv = await session.get(InvestigationRow, investigation_id, with_for_update=True)
            if inv is None or inv.user_id != user_id or inv.active_request_id != request_id or inv.status != "auditing":
                return
            await session.execute(
                update(AuditIssueRow)
                .where(AuditIssueRow.investigation_id == investigation_id, AuditIssueRow.raised_by == f"annotation:{request_id}", AuditIssueRow.claim_id.is_(None))
                .values(status="resolved", resolved_by="coverage_after_audit")
            )

    @staticmethod
    async def finish_in_transaction(session, investigation, report):
        if not investigation.active_request_id:
            return
        request = await session.get(ResearchRequestRow, investigation.active_request_id)
        pending = (
            await session.execute(select(AuditIssueRow.id).where(AuditIssueRow.investigation_id == investigation.id, AuditIssueRow.raised_by == f"annotation:{request.id}", AuditIssueRow.status == "open").limit(1))
        ).scalar_one_or_none()
        request.status = "needs_review" if pending or report.structured_data.get("partial") else "completed"
        request.result_report_id = report.id
        investigation.active_request_id = None
        session.add(InvestigationEventRow(investigation_id=investigation.id, stage="synthesizing", event_type="ci.annotation.completed", payload={"request_id": request.id, "report_id": report.id, "status": request.status}))
