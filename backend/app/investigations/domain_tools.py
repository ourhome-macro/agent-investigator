from __future__ import annotations

from langchain.tools import tool

from app.investigations.domain_runtime import get_domain_submission_handler
from app.investigations.protocols import SubmissionKind
from deerflow.runtime.user_context import resolve_runtime_user_id
from deerflow.tools.types import Runtime


async def _submit(runtime: Runtime, *, task_id: str, kind: SubmissionKind, payload: dict, warnings: list[str] | None) -> str:
    handler = get_domain_submission_handler()
    if handler is None:
        raise RuntimeError("Competitive Research domain submission runtime is unavailable")
    submission = await handler.submit_domain_submission(
        task_id=task_id,
        user_id=resolve_runtime_user_id(runtime),
        kind=kind,
        payload=payload,
        warnings=warnings,
    )
    return submission.model_dump_json()


@tool("submit_scope")
async def submit_scope_tool(runtime: Runtime, task_id: str, scope: dict, warnings: list[str] | None = None) -> str:
    """Submit one typed Competitive Research scope proposal for the active task."""

    return await _submit(runtime, task_id=task_id, kind=SubmissionKind.SCOPE, payload={"scope": scope}, warnings=warnings)


@tool("submit_evidence")
async def submit_evidence_tool(runtime: Runtime, task_id: str, evidence: list[dict], warnings: list[str] | None = None) -> str:
    """Submit retrieved Evidence candidates for the active Competitive Research task."""

    return await _submit(runtime, task_id=task_id, kind=SubmissionKind.EVIDENCE, payload={"evidence": evidence}, warnings=warnings)


@tool("submit_claims")
async def submit_claims_tool(runtime: Runtime, task_id: str, claims: list[dict], warnings: list[str] | None = None) -> str:
    """Submit Claims with verbatim Evidence bindings for the active Competitive Research task."""

    return await _submit(runtime, task_id=task_id, kind=SubmissionKind.CLAIMS, payload={"claims": claims}, warnings=warnings)


@tool("submit_audit")
async def submit_audit_tool(
    runtime: Runtime,
    task_id: str,
    binding_verdicts: list[dict],
    issues: list[dict],
    warnings: list[str] | None = None,
) -> str:
    """Submit Claim-Evidence entailment verdicts and Audit Issues for the active task."""

    return await _submit(
        runtime,
        task_id=task_id,
        kind=SubmissionKind.AUDIT,
        payload={"binding_verdicts": binding_verdicts, "issues": issues},
        warnings=warnings,
    )


@tool("submit_report_section")
async def submit_report_section_tool(runtime: Runtime, task_id: str, sections: list[dict], warnings: list[str] | None = None) -> str:
    """Submit all structured report sections for the active Competitive Research task."""

    return await _submit(runtime, task_id=task_id, kind=SubmissionKind.REPORT, payload={"sections": sections}, warnings=warnings)
