from __future__ import annotations

import pytest

from app.investigations.embeddings import OpenAICompatibleEmbeddingProvider, build_embedding_provider
from app.investigations.retrieval import rank_chunks


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self):
        return {"data": [{"index": 1, "embedding": [0.0, 1.0]}, {"index": 0, "embedding": [1.0, 0.0]}]}


class _Client:
    def __init__(self, **kwargs) -> None:
        self.kwargs = kwargs

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return None

    async def post(self, url, **kwargs):
        assert url == "https://embedding.example/v1/embeddings"
        assert kwargs["json"]["input"] == ["first", "second"]
        return _Response()


@pytest.mark.asyncio
async def test_openai_compatible_embedding_provider_preserves_input_order(monkeypatch) -> None:
    monkeypatch.setattr("app.investigations.embeddings.httpx.AsyncClient", _Client)
    provider = OpenAICompatibleEmbeddingProvider(
        base_url="https://embedding.example/v1",
        api_key="secret",
        model="embedding-model",
    )
    assert await provider.embed_texts(["first", "second"]) == [[1.0, 0.0], [0.0, 1.0]]


def test_embedding_configuration_is_all_or_nothing(monkeypatch) -> None:
    monkeypatch.setenv("CI_EMBEDDING_BASE_URL", "https://embedding.example/v1")
    monkeypatch.delenv("CI_EMBEDDING_API_KEY", raising=False)
    monkeypatch.delenv("CI_EMBEDDING_MODEL", raising=False)
    with pytest.raises(RuntimeError, match="configured together"):
        build_embedding_provider()


def test_hybrid_ranker_uses_semantic_embedding_when_dimensions_match() -> None:
    chunks = [
        {"id": "semantic", "content": "unmatched wording", "embedding": [1.0, 0.0]},
        {"id": "other", "content": "unmatched wording", "embedding": [0.0, 1.0]},
    ]
    ranked = rank_chunks("query", chunks, query_embedding=[1.0, 0.0])
    assert ranked[0]["id"] == "semantic"
