from __future__ import annotations

import pytest

from app.investigations.contracts import SourceType
from app.investigations.providers import ResearchProviderRegistry, classify_source_type


def test_source_type_is_classified_server_side() -> None:
    assert classify_source_type("https://github.com/acme/project") == SourceType.GITHUB
    assert classify_source_type("https://docs.acme.com/api/start") == SourceType.DOCUMENTATION
    assert classify_source_type("https://acme.com/pricing") == SourceType.PRICING
    assert classify_source_type("https://www.36kr.com/p/123") == SourceType.NEWS


@pytest.mark.asyncio
async def test_pricing_page_uses_high_priority_browser_result(monkeypatch) -> None:
    browser_calls: list[str] = []

    async def browser_fetch(url: str, investigation_id: str) -> str:
        browser_calls.append(f"{investigation_id}:{url}")
        return "Pro 套餐每月人民币 ¥199，按账户计费。"

    async def direct_fetch(url: str) -> str:
        return "Pricing"

    providers = ResearchProviderRegistry(browser_fetch=browser_fetch)
    providers._jina_key = None
    monkeypatch.setattr(providers, "_fetch_direct", direct_fetch)
    document = await providers.fetch_url("https://example.com/pricing", investigation_id="investigation-1")
    assert document.source_type == SourceType.PRICING
    assert document.extraction_method == "playwright"
    assert "199" in document.content
    assert browser_calls


@pytest.mark.asyncio
async def test_source_router_rejects_private_network_urls() -> None:
    providers = ResearchProviderRegistry(browser_fetch=None)
    with pytest.raises(RuntimeError, match="private|loopback"):
        await providers.fetch_url("http://127.0.0.1/internal")
