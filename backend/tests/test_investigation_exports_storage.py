from __future__ import annotations

import pytest

from app.investigations.exports import render_report_html
from app.investigations.storage import LocalArtifactStorage


@pytest.mark.asyncio
async def test_local_artifact_storage_round_trip_and_path_guard(tmp_path) -> None:
    storage = LocalArtifactStorage(tmp_path / "artifacts")
    reference = await storage.put_bytes("inv-1/snapshots/a.txt", b"evidence", content_type="text/plain")
    assert reference == "local://inv-1/snapshots/a.txt"
    assert await storage.get_bytes(reference) == b"evidence"
    with pytest.raises(ValueError, match="escapes"):
        await storage.put_bytes("../secret.txt", b"no", content_type="text/plain")


def test_report_html_escapes_untrusted_markdown() -> None:
    document = render_report_html(
        {
            "version": 1,
            "structured_data": {"title": "Acme <script>"},
            "rendered_markdown": "# Report\n\n<script>alert(1)</script>",
        }
    )
    assert "<script>alert" not in document
    assert "&lt;script&gt;alert" in document
