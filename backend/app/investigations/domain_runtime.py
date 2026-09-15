from __future__ import annotations

import threading
from typing import Protocol

from app.investigations.protocols import DomainSubmission, SubmissionKind


class DomainSubmissionHandler(Protocol):
    async def submit_domain_submission(
        self,
        *,
        task_id: str,
        user_id: str,
        kind: SubmissionKind,
        payload: dict,
        warnings: list[str] | None = None,
    ) -> DomainSubmission: ...


_handler: DomainSubmissionHandler | None = None
_lock = threading.Lock()


def set_domain_submission_handler(handler: DomainSubmissionHandler | None) -> None:
    global _handler
    with _lock:
        _handler = handler


def get_domain_submission_handler() -> DomainSubmissionHandler | None:
    with _lock:
        return _handler
