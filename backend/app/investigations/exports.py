from __future__ import annotations

import asyncio
import hashlib
import html
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any

from app.investigations.storage import ArtifactStorage


class PdfExportUnavailable(RuntimeError):
    pass


class ReportExportService:
    def __init__(self, storage: ArtifactStorage) -> None:
        self._storage = storage

    async def render_pdf(self, investigation_id: str, report: dict[str, Any]) -> tuple[bytes, str, str]:
        executable = _find_chromium()
        if executable is None:
            raise PdfExportUnavailable("Chromium or Microsoft Edge is required for server-side PDF export")
        document = render_report_html(report)
        with tempfile.TemporaryDirectory(prefix="deerflow-ci-pdf-") as temp_dir:
            directory = Path(temp_dir)
            html_path = directory / "report.html"
            pdf_path = directory / "report.pdf"
            await asyncio.to_thread(html_path.write_text, document, encoding="utf-8")
            process = await asyncio.create_subprocess_exec(
                executable,
                "--headless",
                "--disable-gpu",
                "--no-pdf-header-footer",
                "--disable-extensions",
                "--disable-sync",
                f"--print-to-pdf={pdf_path}",
                html_path.as_uri(),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            try:
                _, stderr = await asyncio.wait_for(process.communicate(), timeout=90)
            except TimeoutError:
                process.kill()
                await process.wait()
                raise PdfExportUnavailable("Chromium PDF export timed out") from None
            if process.returncode != 0 or not pdf_path.exists():
                raise PdfExportUnavailable(f"Chromium PDF export failed: {stderr.decode(errors='replace')[:500]}")
            content = await asyncio.to_thread(pdf_path.read_bytes)
        if not content.startswith(b"%PDF"):
            raise PdfExportUnavailable("Chromium returned an invalid PDF")
        digest = hashlib.sha256(content).hexdigest()
        reference = await self._storage.put_bytes(
            f"{investigation_id}/exports/report-v{report['version']}-{digest[:12]}.pdf",
            content,
            content_type="application/pdf",
        )
        return content, reference, digest


def render_report_html(report: dict[str, Any]) -> str:
    title = html.escape(str((report.get("structured_data") or {}).get("title") or "Competitive Research Report"))
    body = _markdown_to_html(str(report.get("rendered_markdown") or ""))
    return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><title>{title}</title>
<style>
@page {{ size: A4; margin: 18mm 16mm; }}
body {{ font-family: "Noto Sans CJK SC", "Microsoft YaHei", "PingFang SC", sans-serif; color:#172033; font-size:11pt; line-height:1.65; }}
h1 {{ font-size:24pt; border-bottom:2px solid #1f4f8f; padding-bottom:8px; }}
h2 {{ font-size:16pt; margin-top:24px; color:#173f73; break-after:avoid; }}
li {{ margin:5px 0; }} a {{ color:#175f9e; overflow-wrap:anywhere; }}
.meta {{ color:#68758a; font-size:9pt; }}
</style></head><body><div class="meta">Structured Competitive Research · Report v{report.get("version")}</div>{body}</body></html>"""


def _markdown_to_html(markdown: str) -> str:
    output: list[str] = []
    in_list = False
    for raw in markdown.splitlines():
        line = raw.strip()
        if line.startswith("# "):
            if in_list:
                output.append("</ul>")
                in_list = False
            output.append(f"<h1>{html.escape(line[2:])}</h1>")
        elif line.startswith("## "):
            if in_list:
                output.append("</ul>")
                in_list = False
            output.append(f"<h2>{html.escape(line[3:])}</h2>")
        elif line.startswith("- "):
            if not in_list:
                output.append("<ul>")
                in_list = True
            output.append(f"<li>{html.escape(line[2:])}</li>")
        elif line:
            if in_list:
                output.append("</ul>")
                in_list = False
            output.append(f"<p>{html.escape(line)}</p>")
    if in_list:
        output.append("</ul>")
    return "\n".join(output)


def _find_chromium() -> str | None:
    configured = os.getenv("CI_CHROMIUM_PATH")
    candidates = [
        configured,
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("google-chrome"),
        shutil.which("msedge"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    ]
    return next((str(path) for path in candidates if path and Path(path).is_file()), None)
