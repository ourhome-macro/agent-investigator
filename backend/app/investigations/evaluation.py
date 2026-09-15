from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from app.investigations.evidence_validation import EvidenceValidationError, locate_verbatim_quote, require_claim_values_in_quotes

EntailmentLabel = Literal["entails", "partially_supports", "contradicts", "unrelated", "inaccessible"]


class GoldenEvidence(BaseModel):
    source_id: str
    snapshot: str
    quote: str | None = None


class GoldenCase(BaseModel):
    case_id: str
    category: str
    prompt: str
    claim: str
    evidence: list[GoldenEvidence] = Field(min_length=1)
    gold_label: EntailmentLabel
    should_publish: bool


@dataclass(frozen=True)
class EvaluationScore:
    total: int
    label_accuracy: float
    publish_precision: float
    unsupported_publish_count: int


def load_golden_cases(path: str | Path) -> list[GoldenCase]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("Golden dataset must be a JSON array")
    cases = [GoldenCase.model_validate(item) for item in payload]
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("Golden case IDs must be unique")
    return cases


def deterministic_case_gate(case: GoldenCase) -> bool:
    quotes: list[str] = []
    for evidence in case.evidence:
        if evidence.quote is None:
            continue
        try:
            quotes.append(locate_verbatim_quote(evidence.snapshot, evidence.quote).quote)
        except EvidenceValidationError:
            return False
    if not quotes:
        return False
    try:
        require_claim_values_in_quotes(case.claim, quotes)
    except EvidenceValidationError:
        return False
    return True


def score_predictions(cases: list[GoldenCase], predictions: dict[str, dict[str, object]]) -> EvaluationScore:
    correct = 0
    published = 0
    correct_published = 0
    unsupported_publish_count = 0
    for case in cases:
        prediction = predictions.get(case.case_id, {})
        label = prediction.get("label")
        publish = prediction.get("publish") is True
        correct += label == case.gold_label
        if publish:
            published += 1
            if case.should_publish:
                correct_published += 1
            else:
                unsupported_publish_count += 1
    return EvaluationScore(
        total=len(cases),
        label_accuracy=correct / max(1, len(cases)),
        publish_precision=correct_published / max(1, published),
        unsupported_publish_count=unsupported_publish_count,
    )
