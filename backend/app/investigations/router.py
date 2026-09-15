from __future__ import annotations

import asyncio
import hashlib
import re
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, File, HTTPException, Query, Request, Response, UploadFile, status

from app.investigations.contracts import ApprovalRequest, ClaimCreate, EvidenceCreate, InvestigationCreate, InvestigationStatus, ReworkRequest, ScopePatch
from app.investigations.evidence_validation import sha256_text
from app.investigations.repository import InvestigationConflict, InvestigationRepository
from deerflow.runtime.user_context import get_effective_user_id
from deerflow.utils.file_conversion import CONVERTIBLE_EXTENSIONS, convert_file_to_markdown

router = APIRouter(prefix="/api/investigations", tags=["investigations"])


def _repo(request: Request) -> InvestigationRepository:
    repo = getattr(request.app.state, "investigation_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Competitive Research persistence is not available")
    return repo


def _orchestration_repo(request: Request):
    repo = getattr(request.app.state, "investigation_orchestration_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Competitive Research orchestration is not available")
    return repo


def _user_id() -> str:
    user_id = get_effective_user_id()
    if not user_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    return user_id


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Investigation not found")


def _conflict(exc: Exception) -> HTTPException:
    return HTTPException(status_code=409, detail=str(exc))


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_investigation(body: InvestigationCreate, request: Request):
    service = getattr(request.app.state, "investigation_workflow_service", None)
    if service is None or not service.provider_status.get("durable_orchestration"):
        raise HTTPException(status_code=503, detail="Competitive Research requires an available durable workflow runtime")
    result = await _repo(request).create(body, user_id=_user_id())
    service = getattr(request.app.state, "investigation_workflow_service", None)
    if service is not None:
        service.enqueue(result["id"], _user_id())
    return result


@router.get("")
async def list_investigations(request: Request, limit: int = Query(default=50, ge=1, le=100)):
    return await _repo(request).list(user_id=_user_id(), limit=limit)


@router.get("/providers/status")
async def provider_status(request: Request):
    service = getattr(request.app.state, "investigation_workflow_service", None)
    if service is None:
        raise HTTPException(status_code=503, detail="Competitive Research workflow is not available")
    return service.provider_status


@router.get("/{investigation_id}")
async def get_investigation(investigation_id: str, request: Request):
    result = await _repo(request).get(investigation_id, user_id=_user_id())
    if result is None:
        raise _not_found()
    return result


@router.patch("/{investigation_id}/scope")
async def patch_scope(investigation_id: str, body: ScopePatch, request: Request):
    try:
        result = await _repo(request).update_scope(investigation_id, body.scope, expected_version=body.expected_version, user_id=_user_id())
    except InvestigationConflict as exc:
        raise _conflict(exc) from exc
    if result is None:
        raise _not_found()
    return result


@router.post("/{investigation_id}/scope/approve")
async def approve_scope(investigation_id: str, body: ApprovalRequest, request: Request):
    try:
        result = await _repo(request).approve_scope(investigation_id, user_id=_user_id(), idempotency_key=body.idempotency_key)
    except (InvestigationConflict, ValueError) as exc:
        raise _conflict(exc) from exc
    if result is None:
        raise _not_found()
    service = getattr(request.app.state, "investigation_workflow_service", None)
    if service is not None:
        service.enqueue(investigation_id, _user_id())
    return result


@router.post("/{investigation_id}/cancel", status_code=status.HTTP_202_ACCEPTED)
async def cancel_investigation(investigation_id: str, request: Request):
    repo = _repo(request)
    try:
        result = await repo.transition(investigation_id, target=InvestigationStatus.CANCELLING, user_id=_user_id(), event_type="ci.stage.progress", payload={"action": "cancel"})
        if result is not None:
            result = await repo.transition(investigation_id, target=InvestigationStatus.CANCELLED, user_id=_user_id(), event_type="ci.stage.completed")
    except ValueError as exc:
        raise _conflict(exc) from exc
    if result is None:
        raise _not_found()
    return result


@router.post("/{investigation_id}/retry", status_code=status.HTTP_202_ACCEPTED)
async def retry_failed_investigation(investigation_id: str, body: ApprovalRequest, request: Request):
    user_id = _user_id()
    try:
        result = await _repo(request).retry_failed(
            investigation_id,
            user_id=user_id,
            idempotency_key=body.idempotency_key,
        )
    except (InvestigationConflict, RuntimeError) as exc:
        raise _conflict(exc) from exc
    if result is None:
        raise _not_found()
    service = getattr(request.app.state, "investigation_workflow_service", None)
    if service is not None:
        service.enqueue(investigation_id, user_id)
    return result


@router.get("/{investigation_id}/events")
async def list_events(investigation_id: str, request: Request, after_seq: int = Query(default=0, ge=0), limit: int = Query(default=500, ge=1, le=2000)):
    result = await _repo(request).list_events(investigation_id, user_id=_user_id(), after_seq=after_seq, limit=limit)
    if result is None:
        raise _not_found()
    return result


@router.get("/{investigation_id}/orchestration/items")
async def list_orchestration_items(investigation_id: str, request: Request):
    result = await _orchestration_repo(request).list_stage_items(investigation_id, user_id=_user_id())
    if result is None:
        raise _not_found()
    return result


@router.post("/{investigation_id}/evidence", status_code=status.HTTP_201_CREATED)
async def submit_evidence(investigation_id: str, body: EvidenceCreate, request: Request):
    try:
        result = await _repo(request).add_evidence(investigation_id, body, user_id=_user_id())
    except InvestigationConflict as exc:
        raise _conflict(exc) from exc
    if result is None:
        raise _not_found()
    return result


@router.get("/{investigation_id}/evidence")
async def list_evidence(investigation_id: str, request: Request):
    result = await _repo(request).list_evidence(investigation_id, user_id=_user_id())
    if result is None:
        raise _not_found()
    return result


@router.post("/{investigation_id}/claims", status_code=status.HTTP_201_CREATED)
async def submit_claim(investigation_id: str, body: ClaimCreate, request: Request):
    try:
        result = await _repo(request).add_claim(investigation_id, body, user_id=_user_id())
    except InvestigationConflict as exc:
        raise _conflict(exc) from exc
    if result is None:
        raise _not_found()
    return result


@router.get("/{investigation_id}/claims")
async def list_claims(investigation_id: str, request: Request):
    result = await _repo(request).list_claims(investigation_id, user_id=_user_id())
    if result is None:
        raise _not_found()
    return result


@router.get("/{investigation_id}/audit/issues")
async def list_audit_issues(investigation_id: str, request: Request, status_filter: str | None = Query(default=None, alias="status")):
    result = await _repo(request).list_audit_issues(investigation_id, user_id=_user_id(), status=status_filter)
    if result is None:
        raise _not_found()
    return result


@router.get("/{investigation_id}/pricing")
async def list_pricing(investigation_id: str, request: Request):
    result = await _repo(request).list_price_observations(investigation_id, user_id=_user_id())
    if result is None:
        raise _not_found()
    return result


@router.get("/{investigation_id}/coverage")
async def get_coverage(investigation_id: str, request: Request):
    from app.investigations.quality import coverage_cells

    repo = _repo(request)
    owner = _user_id()
    investigation = await repo.get(investigation_id, user_id=owner)
    if investigation is None:
        raise _not_found()
    return coverage_cells(
        await repo.list_competitors(investigation_id, user_id=owner) or [],
        investigation["scope"]["dimensions"],
        await repo.list_claims(investigation_id, user_id=owner) or [],
        await repo.list_audit_issues(investigation_id, user_id=owner, status="open") or [],
    )


@router.post("/{investigation_id}/materials", status_code=status.HTTP_201_CREATED)
async def upload_material(investigation_id: str, request: Request, file: UploadFile = File(...)):
    user_id = _user_id()
    investigation = await _repo(request).get(investigation_id, user_id=user_id)
    if investigation is None:
        raise _not_found()
    if investigation["status"] not in {"planning", "awaiting_scope_approval", "collecting", "reworking"}:
        raise HTTPException(status_code=409, detail="Materials cannot be added in the current investigation stage")
    filename = re.sub(r"[^A-Za-z0-9._\-\u3400-\u9fff]+", "_", Path(file.filename or "material.txt").name)[:180]
    extension = Path(filename).suffix.lower()
    allowed = CONVERTIBLE_EXTENSIONS | {".txt", ".md", ".csv", ".json"}
    if extension not in allowed:
        raise HTTPException(status_code=415, detail=f"Unsupported research material type: {extension or 'none'}")
    content = bytearray()
    while chunk := await file.read(1024 * 1024):
        content.extend(chunk)
        if len(content) > 20 * 1024 * 1024:
            raise HTTPException(status_code=413, detail="Research material exceeds the 20 MB limit")
    storage = getattr(request.app.state, "investigation_artifact_storage", None)
    if storage is None:
        raise HTTPException(status_code=503, detail="Investigation artifact storage is unavailable")
    original_digest = hashlib.sha256(content).hexdigest()
    original_ref = await storage.put_bytes(
        f"{investigation_id}/uploads/{original_digest}-{filename}",
        bytes(content),
        content_type=file.content_type or "application/octet-stream",
    )
    with tempfile.TemporaryDirectory(prefix="deerflow-ci-upload-") as temp_dir:
        source_path = Path(temp_dir) / filename
        await asyncio.to_thread(source_path.write_bytes, bytes(content))
        if extension in {".txt", ".md", ".csv", ".json"}:
            extracted = bytes(content).decode("utf-8", errors="replace")
            extraction_method = "utf8_upload"
        else:
            converted_path = await convert_file_to_markdown(source_path)
            if converted_path is None:
                raise HTTPException(status_code=422, detail="Research material conversion failed")
            extracted = await asyncio.to_thread(converted_path.read_text, encoding="utf-8")
            extraction_method = "markitdown_upload"
    extracted = extracted.strip()
    if len(extracted) < 20:
        raise HTTPException(status_code=422, detail="Research material contains too little extractable text")
    synthetic_url = f"https://uploads.invalid/{investigation_id}/{quote(filename)}"
    try:
        result = await _repo(request).add_evidence(
            investigation_id,
            EvidenceCreate(
                source_url=synthetic_url,
                canonical_url=synthetic_url,
                source_domain="uploads.invalid",
                source_type="user_upload",
                title=filename,
                retrieved_at=datetime.now(UTC),
                excerpt=extracted[:20_000],
                snapshot_text=extracted,
                content_hash=sha256_text(extracted),
                extraction_method=extraction_method,
                original_ref=original_ref,
                source_authority=18,
                freshness=10,
                extraction_quality=8,
                specificity=7,
                corroboration=0,
            ),
            user_id=user_id,
            agent_name="user-upload",
        )
    except InvestigationConflict as exc:
        raise _conflict(exc) from exc
    return result


@router.get("/{investigation_id}/reports/latest")
async def latest_report(investigation_id: str, request: Request):
    report = await _repo(request).latest_report(investigation_id, user_id=_user_id())
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    return report


@router.post("/{investigation_id}/reports/{version}/approve")
async def approve_report(investigation_id: str, version: int, body: ApprovalRequest, request: Request):
    try:
        report = await _repo(request).approve_report(investigation_id, version, user_id=_user_id(), idempotency_key=body.idempotency_key)
    except (InvestigationConflict, ValueError) as exc:
        raise _conflict(exc) from exc
    if report is None:
        raise _not_found()
    return report


async def _start_rework(investigation_id: str, body: ReworkRequest, request: Request, *, section_id: str | None = None):
    user_id = _user_id()
    report = await _repo(request).latest_report(investigation_id, user_id=user_id)
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    try:
        result = await _repo(request).reject_report(
            investigation_id,
            report["version"],
            user_id=user_id,
            reason=body.reason,
            idempotency_key=body.idempotency_key,
            section_id=section_id,
        )
        if result is not None:
            result = await _repo(request).transition(
                investigation_id,
                InvestigationStatus.COLLECTING,
                user_id=user_id,
                event_type="ci.stage.started",
                payload={"rework": True, "section_id": section_id},
            )
    except (InvestigationConflict, ValueError) as exc:
        raise _conflict(exc) from exc
    if result is None:
        raise _not_found()
    service = getattr(request.app.state, "investigation_workflow_service", None)
    if service is not None:
        service.enqueue(investigation_id, user_id)
    return result


@router.post("/{investigation_id}/reports/finalize-partial")
async def finalize_partial_report(investigation_id: str, request: Request):
    from app.investigations.partial_report import build_partial_report

    user_id = _user_id()
    repository = _repo(request)
    investigation = await repository.get(investigation_id, user_id=user_id)
    if investigation is None:
        raise _not_found()
    if investigation["status"] != InvestigationStatus.FAILED.value:
        raise HTTPException(status_code=409, detail="Partial finalization is available only for failed Investigations")
    claims = await repository.list_claims(investigation_id, user_id=user_id) or []
    evidence = await repository.list_evidence(investigation_id, user_id=user_id) or []
    issues = await repository.list_audit_issues(investigation_id, user_id=user_id) or []
    structured, markdown = build_partial_report(investigation, claims, evidence, issues)
    return await repository.create_report(
        investigation_id,
        structured_data=structured,
        rendered_markdown=markdown,
        user_id=user_id,
        allow_failed_partial=True,
    )


@router.post("/{investigation_id}/reports/{version}/reject")
async def reject_report(investigation_id: str, version: int, body: ReworkRequest, request: Request):
    report = await _repo(request).latest_report(investigation_id, user_id=_user_id())
    if report is None or report["version"] != version:
        raise HTTPException(status_code=404, detail="Report not found")
    return await _start_rework(investigation_id, body, request)


@router.post("/{investigation_id}/sections/{section_id}/rework")
async def rework_section(investigation_id: str, section_id: str, body: ReworkRequest, request: Request):
    return await _start_rework(investigation_id, body, request, section_id=section_id)


@router.get("/{investigation_id}/exports/{export_format}")
async def export_report(investigation_id: str, export_format: str, request: Request):
    report = await _repo(request).latest_report(investigation_id, user_id=_user_id())
    if report is None:
        raise HTTPException(status_code=404, detail="Report not found")
    if export_format not in {"markdown", "pdf"}:
        raise HTTPException(status_code=422, detail="Unsupported export format")
    if export_format == "pdf":
        from app.investigations.exports import PdfExportUnavailable

        service = getattr(request.app.state, "investigation_export_service", None)
        if service is None:
            raise HTTPException(status_code=503, detail="PDF export service is unavailable")
        try:
            content, object_ref, digest = await service.render_pdf(investigation_id, report)
        except PdfExportUnavailable as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        await _repo(request).record_export(
            investigation_id,
            report_id=report["id"],
            export_format="pdf",
            object_ref=object_ref,
            content_hash=digest,
            user_id=_user_id(),
        )
        return Response(
            content=content,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="competitive-research-{investigation_id}-v{report["version"]}.pdf"'},
        )
    filename = f"competitive-research-{investigation_id}-v{report['version']}.md"
    return Response(
        content=report["rendered_markdown"],
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
