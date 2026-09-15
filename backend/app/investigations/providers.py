from __future__ import annotations

import asyncio
import importlib.util
import os
import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from time import monotonic
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx

from app.investigations.contracts import SourceType
from app.investigations.evidence_validation import EvidenceValidationError, locate_verbatim_quote
from deerflow.community.url_safety import validate_public_http_url


class ProviderUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    content: str
    provider: str
    score: float = 0.0


@dataclass(frozen=True)
class SourceDocument:
    url: str
    content: str
    source_type: SourceType
    extraction_method: str
    mime_type: str = "text/plain"


@dataclass
class _Circuit:
    failures: int = 0
    open_until: float = 0.0


BrowserFetch = Callable[[str, str], Awaitable[str]]


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("Evidence URL must be an absolute HTTP(S) URL")
    kept_query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", urlencode(kept_query), ""))


class ResearchProviderRegistry:
    def __init__(self, *, browser_fetch: BrowserFetch | None = None) -> None:
        self._bocha_key = _usable_key(os.getenv("BOCHA_API_KEY"))
        self._bocha_base = os.getenv("BOCHA_BASE_URL", "https://api.bochaai.com/v1/web-search")
        self._tavily_key = _usable_key(os.getenv("TAVILY_API_KEY"))
        self._jina_key = _usable_key(os.getenv("JINA_API_KEY"))
        self._allow_ddgs = os.getenv("DEER_FLOW_ENV", "development").lower() != "production"
        self._browser_fetch = browser_fetch if browser_fetch is not None else _default_browser_fetch
        self._provider_limit = asyncio.Semaphore(max(1, int(os.getenv("CI_PROVIDER_CONCURRENCY", "4"))))
        self._circuits: dict[str, _Circuit] = {name: _Circuit() for name in ("bocha", "tavily", "jina", "browser", "direct")}

    def status(self) -> dict[str, bool]:
        return {"bocha": bool(self._bocha_key), "tavily": bool(self._tavily_key), "jina": bool(self._jina_key), "ddgs_development_fallback": self._allow_ddgs}

    def validate_startup(self) -> None:
        if self._allow_ddgs:
            return
        missing = []
        if not (self._bocha_key or self._tavily_key):
            missing.append("BOCHA_API_KEY or TAVILY_API_KEY")
        if not self._jina_key:
            missing.append("JINA_API_KEY")
        if missing:
            raise ProviderUnavailable("Competitive Research production providers missing: " + ", ".join(missing))

    async def search(self, query: str, *, max_results: int = 5) -> list[SearchHit]:
        calls = []
        if self._bocha_key:
            calls.append(self._with_retry("bocha", lambda: self._search_bocha(query, max_results=max_results)))
        if self._tavily_key:
            calls.append(self._with_retry("tavily", lambda: self._search_tavily(query, max_results=max_results)))
        if not calls and self._allow_ddgs:
            return await self._with_retry("direct", lambda: self._search_ddgs(query, max_results=max_results))
        if not calls:
            raise ProviderUnavailable("Strict research requires BOCHA_API_KEY or TAVILY_API_KEY")
        batches = await asyncio.gather(*calls, return_exceptions=True)
        hits: list[SearchHit] = []
        errors: list[str] = []
        for batch in batches:
            if isinstance(batch, BaseException):
                errors.append(str(batch))
            else:
                hits.extend(batch)
        if not hits:
            raise ProviderUnavailable("All configured search providers failed: " + "; ".join(errors))
        unique: dict[str, SearchHit] = {}
        for hit in hits:
            try:
                unique.setdefault(canonicalize_url(hit.url), hit)
            except ValueError:
                continue
        return list(unique.values())[:max_results]

    async def fetch(self, hit: SearchHit) -> str:
        return (await self.fetch_url(hit.url, fallback_content=hit.content)).content

    async def fetch_url(
        self,
        url: str,
        *,
        fallback_content: str = "",
        investigation_id: str = "competitive-research",
    ) -> SourceDocument:
        canonical = canonicalize_url(url)
        safety_error = validate_public_http_url(canonical, action="competitive research fetch")
        if safety_error:
            raise ProviderUnavailable(safety_error)
        source_type = classify_source_type(canonical)
        candidates: list[SourceDocument] = []
        if self._jina_key and self._circuit_allows("jina"):
            try:
                content = await self._with_retry("jina", lambda: self._fetch_jina(canonical))
                if content.strip():
                    candidates.append(SourceDocument(canonical, content[:500_000], source_type, "jina"))
            except Exception:
                pass
        if not candidates or _needs_browser(candidates[0].content, source_type):
            try:
                direct = await self._with_retry("direct", lambda: self._fetch_direct(canonical))
                if direct.strip():
                    candidates.append(SourceDocument(canonical, direct[:500_000], source_type, "direct_html"))
            except Exception:
                pass
        if source_type == SourceType.PRICING or not candidates or all(_low_quality(item.content) for item in candidates):
            if self._browser_fetch is not None and self._circuit_allows("browser"):
                try:
                    rendered = await self._with_retry("browser", lambda: self._browser_fetch(canonical, investigation_id), attempts=1)
                    if rendered.strip():
                        candidates.append(SourceDocument(canonical, rendered[:500_000], source_type, "playwright"))
                except Exception:
                    pass
        if fallback_content.strip():
            candidates.append(SourceDocument(canonical, fallback_content[:500_000], source_type, "search_snippet"))
        if not candidates:
            raise ProviderUnavailable(f"No extractor produced content for {canonical}")
        matching: list[SourceDocument] = []
        if fallback_content.strip():
            for candidate in candidates:
                try:
                    locate_verbatim_quote(candidate.content, fallback_content)
                    matching.append(candidate)
                except EvidenceValidationError:
                    continue
        return max(matching or candidates, key=lambda item: _document_quality(item.content, source_type))

    async def _fetch_jina(self, url: str) -> str:
        headers = {"Authorization": f"Bearer {self._jina_key}", "Accept": "text/plain"}
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            response = await client.get(f"https://r.jina.ai/{url}", headers=headers)
            response.raise_for_status()
            return response.text

    async def _fetch_direct(self, url: str) -> str:
        async with httpx.AsyncClient(timeout=20.0, follow_redirects=False, headers={"User-Agent": "DeerFlow-CompetitiveResearch/1.0"}) as client:
            response = await client.get(url)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "")
            if "text/" not in content_type and "json" not in content_type:
                raise ProviderUnavailable(f"Unsupported content type: {content_type}")
            if "html" not in content_type:
                return response.text
            parser = _VisibleTextParser()
            parser.feed(response.text[:2_000_000])
            return parser.text()

    def _circuit_allows(self, provider: str) -> bool:
        return self._circuits[provider].open_until <= monotonic()

    async def _with_retry(self, provider: str, operation: Callable[[], Awaitable], *, attempts: int = 3):
        if not self._circuit_allows(provider):
            raise ProviderUnavailable(f"Provider circuit is open: {provider}")
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                async with self._provider_limit:
                    result = await operation()
                self._circuits[provider] = _Circuit()
                return result
            except Exception as exc:
                last_error = exc
                if attempt + 1 < attempts:
                    await asyncio.sleep(min(2.0, 0.25 * 2**attempt))
        circuit = self._circuits[provider]
        circuit.failures += 1
        if circuit.failures >= 3:
            circuit.open_until = monotonic() + 60.0
        raise ProviderUnavailable(f"Provider {provider} failed: {last_error}") from last_error

    async def _search_tavily(self, query: str, *, max_results: int) -> list[SearchHit]:
        from tavily import AsyncTavilyClient

        response = await AsyncTavilyClient(api_key=self._tavily_key).search(query=query, search_depth="advanced", max_results=max_results, include_raw_content=True)
        return [
            SearchHit(title=str(item.get("title") or item.get("url") or "Untitled"), url=str(item.get("url") or ""), content=str(item.get("raw_content") or item.get("content") or ""), provider="tavily", score=float(item.get("score") or 0))
            for item in response.get("results", [])
            if item.get("url")
        ]

    async def _search_bocha(self, query: str, *, max_results: int) -> list[SearchHit]:
        headers = {"Authorization": f"Bearer {self._bocha_key}", "Content-Type": "application/json"}
        payload = {"query": query, "freshness": "oneYear", "summary": True, "count": max_results}
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(self._bocha_base, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()
        values = ((data.get("data") or {}).get("webPages") or {}).get("value") or []
        return [SearchHit(title=str(item.get("name") or item.get("url") or "Untitled"), url=str(item.get("url") or ""), content=str(item.get("summary") or item.get("snippet") or ""), provider="bocha") for item in values if item.get("url")]

    async def _search_ddgs(self, query: str, *, max_results: int) -> list[SearchHit]:
        from ddgs import DDGS

        rows = await asyncio.to_thread(lambda: list(DDGS().text(query, max_results=max_results)))
        return [SearchHit(title=str(item.get("title") or item.get("href") or "Untitled"), url=str(item.get("href") or ""), content=str(item.get("body") or ""), provider="ddgs") for item in rows if item.get("href")]


def _usable_key(value: str | None) -> str | None:
    if not value or value.startswith("your-"):
        return None
    return value


def classify_source_type(url: str) -> SourceType:
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    path = parts.path.lower()
    if host == "github.com" or host.endswith(".github.com"):
        return SourceType.GITHUB
    if any(token in path for token in ("pricing", "price", "plans", "billing", "套餐", "价格")):
        return SourceType.PRICING
    if host.startswith("docs.") or any(token in path for token in ("/docs/", "/documentation/", "/developer/", "/api/")):
        return SourceType.DOCUMENTATION
    if any(domain in host for domain in ("36kr.com", "huxiu.com", "techcrunch.com", "reuters.com", "bloomberg.com")):
        return SourceType.NEWS
    return SourceType.WEB


def _low_quality(content: str) -> bool:
    compact = " ".join(content.split())
    return len(compact) < 240


def _needs_browser(content: str, source_type: SourceType) -> bool:
    return source_type == SourceType.PRICING or _low_quality(content)


def _document_quality(content: str, source_type: SourceType) -> tuple[int, int]:
    compact = " ".join(content.split())
    pricing_markers = sum(marker in compact.casefold() for marker in ("¥", "￥", "$", "usd", "cny", "每月", "每年", "month", "year"))
    return (pricing_markers if source_type == SourceType.PRICING else 0, len(compact))


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip = 0
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"}:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript", "svg"} and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip and data.strip():
            self._parts.append(data.strip())

    def text(self) -> str:
        return "\n".join(self._parts)


async def _default_browser_fetch(url: str, investigation_id: str) -> str:
    if importlib.util.find_spec("playwright") is not None:
        from deerflow.community.browser_automation.session import get_browser_session_manager

        manager = get_browser_session_manager()
        thread_id = f"ci-fetch-{investigation_id}"
        session = manager.get_session(
            thread_id,
            headless=True,
            timeout_ms=30_000,
            url_guard=lambda candidate: validate_public_http_url(candidate, action="competitive research browser fetch"),
            pin=True,
        )
        try:
            await session.navigate(url)
            return await session.get_text(max_chars=500_000)
        finally:
            manager.release_session(thread_id, session)
    candidates = [
        os.getenv("CI_CHROMIUM_PATH"),
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("google-chrome"),
        shutil.which("msedge"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ]
    executable = next((str(candidate) for candidate in candidates if candidate and Path(candidate).is_file()), None)
    if executable is None:
        raise ProviderUnavailable("Neither Playwright nor a Chromium executable is available")
    if os.getenv("DEER_FLOW_ENV", "development").lower() == "production":
        raise ProviderUnavailable("Production SPA extraction requires Playwright so redirects remain SSRF-guarded")
    process = await asyncio.create_subprocess_exec(
        executable,
        "--headless",
        "--disable-gpu",
        "--disable-extensions",
        "--dump-dom",
        url,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), timeout=45)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise ProviderUnavailable("Chromium page extraction timed out") from None
    if process.returncode != 0:
        raise ProviderUnavailable("Chromium page extraction failed")
    parser = _VisibleTextParser()
    parser.feed(stdout.decode("utf-8", errors="replace")[:2_000_000])
    return parser.text()
