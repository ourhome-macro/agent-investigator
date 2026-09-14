from __future__ import annotations

from app.investigations.contracts import InvestigationStatus


class InvalidInvestigationTransition(ValueError):
    pass


_FORWARD: dict[InvestigationStatus, frozenset[InvestigationStatus]] = {
    InvestigationStatus.DRAFT: frozenset({InvestigationStatus.PLANNING}),
    InvestigationStatus.PLANNING: frozenset({InvestigationStatus.AWAITING_SCOPE_APPROVAL}),
    InvestigationStatus.AWAITING_SCOPE_APPROVAL: frozenset({InvestigationStatus.COLLECTING}),
    InvestigationStatus.COLLECTING: frozenset({InvestigationStatus.NORMALIZING}),
    InvestigationStatus.NORMALIZING: frozenset({InvestigationStatus.ANALYZING}),
    InvestigationStatus.ANALYZING: frozenset({InvestigationStatus.AUDITING}),
    InvestigationStatus.AUDITING: frozenset({InvestigationStatus.REWORKING, InvestigationStatus.SYNTHESIZING}),
    InvestigationStatus.REWORKING: frozenset({InvestigationStatus.COLLECTING, InvestigationStatus.ANALYZING}),
    InvestigationStatus.SYNTHESIZING: frozenset({InvestigationStatus.AWAITING_PUBLISH_APPROVAL}),
    InvestigationStatus.AWAITING_PUBLISH_APPROVAL: frozenset({InvestigationStatus.REWORKING, InvestigationStatus.PUBLISHED}),
    InvestigationStatus.CANCELLING: frozenset({InvestigationStatus.CANCELLED}),
}

_CANCELLABLE = frozenset(_FORWARD) - {InvestigationStatus.CANCELLING}
_TERMINAL = frozenset({InvestigationStatus.PUBLISHED, InvestigationStatus.CANCELLED, InvestigationStatus.FAILED})


def require_transition(current: InvestigationStatus, target: InvestigationStatus) -> None:
    if current == target:
        return
    if target == InvestigationStatus.FAILED and current not in _TERMINAL:
        return
    if target == InvestigationStatus.CANCELLING and current in _CANCELLABLE:
        return
    if target not in _FORWARD.get(current, frozenset()):
        raise InvalidInvestigationTransition(f"Cannot transition investigation from {current.value} to {target.value}")


def is_terminal(status: InvestigationStatus) -> bool:
    return status in _TERMINAL
