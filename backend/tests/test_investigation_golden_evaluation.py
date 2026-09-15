from pathlib import Path

from app.investigations.evaluation import deterministic_case_gate, load_golden_cases, score_predictions

FIXTURE = Path(__file__).parent / "fixtures" / "competitive_research_golden.v1.json"


def test_golden_dataset_covers_hallucination_and_extraction_cases() -> None:
    cases = load_golden_cases(FIXTURE)
    assert len(cases) >= 12
    categories = {case.category for case in cases}
    assert {"insufficient_evidence", "pricing_period", "extraction_failure", "prompt_injection", "exact_value"} <= categories
    assert deterministic_case_gate(next(case for case in cases if case.case_id == "exact-supported-feature"))
    assert not deterministic_case_gate(next(case for case in cases if case.case_id == "fabricated-seat-count"))


def test_evaluator_penalizes_unsupported_publication() -> None:
    cases = load_golden_cases(FIXTURE)
    predictions = {case.case_id: {"label": case.gold_label, "publish": case.should_publish} for case in cases}
    predictions["insufficient-single-source"] = {"label": "entails", "publish": True}
    score = score_predictions(cases, predictions)
    assert score.unsupported_publish_count == 1
    assert score.publish_precision < 1
    assert score.label_accuracy < 1
