from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx


class ProviderUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class SearchHit:
    title: str
    url: str
    content: str
    provider: str
    score: float = 0.0


def canonicalize_url(url: str) -> str:
    parts = urlsplit(url.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("Evidence URL must be an absolute HTTP(S) URL")
    kept_query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if not key.lower().startswith("utm_") and key.lower() not in {"fbclid", "gclid"}]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", urlencode(kept_query), ""))


class ResearchProviderRegistry:
    def __init__(self) -> None:
        self._bocha_key = _usable_key(os.getenv("BOCHA_API_KEY"))
        self._bocha_base = os.getenv("BOCHA_BASE_URL", "https://api.bochaai.com/v1/web-search")
        self._tavily_key = _usable_key(os.getenv("TAVILY_API_KEY"))
        self._jina_key = _usable_key(os.getenv("JINA_API_KEY"))
        self._allow_ddgs = os.getenv("DEER_FLOW_ENV", "development").lower() != "production"

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
            calls.append(self._search_bocha(query, max_results=max_results))
        if self._tavily_key:
            calls.append(self._search_tavily(query, max_results=max_results))
        if not calls and self._allow_ddgs:
            return await self._search_ddgs(query, max_results=max_results)
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
        if not self._jina_key:
            return hit.content
        headers = {"Authorization": f"Bearer {self._jina_key}", "Accept": "text/plain"}
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=False) as client:
            response = await client.get(f"https://r.jina.ai/{canonicalize_url(hit.url)}", headers=headers)
            response.raise_for_status()
            return response.text[:100_000]

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
