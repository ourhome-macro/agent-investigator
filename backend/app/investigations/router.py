from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from app.investigations.contracts import ApprovalRequest, ClaimCreate, EvidenceCreate, InvestigationCreate, InvestigationStatus, ReworkRequest, ScopePatch
from app.investigations.repository import InvestigationConflict, InvestigationRepository
from deerflow.runtime.user_context import get_effective_user_id

router = APIRouter(prefix="/api/investigations", tags=["investigations"])


def _repo(request: Request) -> InvestigationRepository:
    repo = getattr(request.app.state, "investigation_repo", None)
    if repo is None:
        raise HTTPException(status_code=503, detail="Competitive Research persistence is not available")
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
    return await _repo(request).create(body, user_id=_user_id())


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


@router.get("/{investigation_id}/events")
async def list_events(investigation_id: str, request: Request, after_seq: int = Query(default=0, ge=0), limit: int = Query(default=500, ge=1, le=2000)):
    result = await _repo(request).list_events(investigation_id, user_id=_user_id(), after_seq=after_seq, limit=limit)
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
    if export_format != "markdown":
        raise HTTPException(status_code=422, detail="Unsupported export format")
    filename = f"competitive-research-{investigation_id}-v{report['version']}.md"
    return Response(
        content=report["rendered_markdown"],
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
