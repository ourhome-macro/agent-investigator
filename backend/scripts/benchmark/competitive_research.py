"""Read-only readiness and historical-recording checks for research control loops.

Run from backend with ``python -m scripts.benchmark.competitive_research``.
Never prints credentials or submits model/search requests.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
from pathlib import Path

from app.investigations.providers import ResearchProviderRegistry
from app.investigations.quality import admit_candidate


def preflight() -> dict:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[3] / ".env")
    status = ResearchProviderRegistry().status()
    required = {"search": status["bocha"] or status["tavily"], "extraction": status["jina"], "semantic_embedding": all(os.getenv(name) for name in ("CI_EMBEDDING_BASE_URL", "CI_EMBEDDING_API_KEY", "CI_EMBEDDING_MODEL"))}
    return {"check": "configuration_presence_only", "ready_for_live_validation": all(required.values()), "required": required, "network_requests": 0}


def inspect_recording(database: Path, investigation_id: str) -> dict:
    if not database.is_file():
        raise ValueError("Recording database does not exist")
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        investigation = connection.execute("SELECT id, status, token_used, token_budget FROM ci_investigations WHERE id = ?", (investigation_id,)).fetchone()
        if investigation is None:
            raise ValueError("Investigation not found")
        scope = connection.execute("SELECT dimensions FROM ci_scopes WHERE investigation_id = ?", (investigation_id,)).fetchone()
        dimensions = json.loads(scope["dimensions"])
        rows = connection.execute(
            "SELECT e.id, e.source_url, e.title, e.excerpt, e.content_hash, c.canonical_name, c.official_domains FROM ci_evidence e LEFT JOIN ci_competitors c ON c.id=e.competitor_id WHERE e.investigation_id=? ORDER BY e.id",
            (investigation_id,),
        ).fetchall()
        results = []
        for row in rows:
            verdicts = [
                admit_candidate(
                    url=row["source_url"],
                    offered_urls=[row["source_url"]],
                    competitor=row["canonical_name"] or "",
                    dimension=dimension,
                    title=row["title"],
                    excerpt=row["excerpt"],
                    official_domains=json.loads(row["official_domains"] or "[]"),
                )
                for dimension in dimensions
            ]
            results.append(
                {
                    "evidence_id": row["id"],
                    "source_url": row["source_url"],
                    "snapshot_sha256": row["content_hash"],
                    "passes_admission_floor": any(verdict.accepted for verdict in verdicts),
                    "reasons": sorted({verdict.reason for verdict in verdicts}),
                }
            )
        report = connection.execute("SELECT structured_data FROM ci_reports WHERE investigation_id=? ORDER BY version DESC LIMIT 1", (investigation_id,)).fetchone()
    manifest = hashlib.sha256(json.dumps(results, sort_keys=True).encode("utf-8")).hexdigest()
    return {
        "check": "historical_admission_replay",
        "investigation": dict(investigation),
        "report_partial": json.loads(report[0]).get("partial") if report else None,
        "evidence_count": len(results),
        "admission_rejected": sum(not item["passes_admission_floor"] for item in results),
        "manifest_sha256": manifest,
        "results": results,
        "limits": "Replay of retained excerpts, not a relevance gold label or a live model evaluation. Original records were not changed.",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["preflight", "inspect-recording"])
    parser.add_argument("--database", type=Path)
    parser.add_argument("--investigation-id")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.command == "preflight":
        result = preflight()
    else:
        if not args.database or not args.investigation_id:
            parser.error("inspect-recording requires --database and --investigation-id")
        result = inspect_recording(args.database, args.investigation_id)
    rendered = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(json.dumps({key: value for key, value in result.items() if key != "results"}, ensure_ascii=True))
    else:
        print(json.dumps(result, ensure_ascii=True, indent=2))
    return 2 if result.get("ready_for_live_validation") is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
