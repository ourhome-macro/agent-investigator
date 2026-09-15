from __future__ import annotations

import os
from typing import Protocol

import httpx


class EmbeddingProvider(Protocol):
    async def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


class OpenAICompatibleEmbeddingProvider:
    def __init__(self, *, base_url: str, api_key: str, model: str) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
            response = await client.post(
                f"{self._base_url}/embeddings",
                headers={"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"},
                json={"model": self._model, "input": texts, "encoding_format": "float"},
            )
            response.raise_for_status()
            payload = response.json()
        rows = sorted(payload.get("data") or [], key=lambda item: int(item.get("index", 0)))
        embeddings = [[float(value) for value in row.get("embedding") or []] for row in rows]
        if len(embeddings) != len(texts) or any(not embedding for embedding in embeddings):
            raise RuntimeError("Embedding provider returned an invalid vector batch")
        if len({len(embedding) for embedding in embeddings}) != 1:
            raise RuntimeError("Embedding provider returned inconsistent vector dimensions")
        return embeddings


def build_embedding_provider() -> EmbeddingProvider | None:
    base_url = os.getenv("CI_EMBEDDING_BASE_URL")
    api_key = os.getenv("CI_EMBEDDING_API_KEY")
    model = os.getenv("CI_EMBEDDING_MODEL")
    if not any((base_url, api_key, model)):
        return None
    if not all((base_url, api_key, model)):
        raise RuntimeError("CI_EMBEDDING_BASE_URL, CI_EMBEDDING_API_KEY, and CI_EMBEDDING_MODEL must be configured together")
    return OpenAICompatibleEmbeddingProvider(base_url=base_url or "", api_key=api_key or "", model=model or "")
