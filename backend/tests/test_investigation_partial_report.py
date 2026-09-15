from app.investigations.partial_report import build_partial_report


def test_partial_report_preserves_uncertainty_and_all_evidence() -> None:
    investigation = {
        "id": "investigation-1",
        "title": "Player research",
        "scope": {
            "market": "China",
            "time_range": "2026",
            "competitors": ["Acme", "Beta"],
        },
    }
    claims = [
        {
            "id": "claim-1",
            "status": "uncertain",
            "dimension": "features",
            "text": "Evidence remains incomplete.",
            "evidence_ids": ["evidence-1"],
        }
    ]
    evidence = [
        {
            "id": "evidence-1",
            "title": "Source",
            "source_domain": "example.com",
            "source_url": "https://example.com/source",
            "credibility_score": 50,
        }
    ]
    structured, markdown = build_partial_report(investigation, claims, evidence, [])
    assert structured["partial"] is True
    assert len(structured["sections"]) == 11
    assert "[uncertain]" in markdown
    assert "https://example.com/source" in markdown
