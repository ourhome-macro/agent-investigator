from __future__ import annotations

import math
import re
from dataclasses import dataclass
from hashlib import blake2b
from typing import Any

from app.investigations.evidence_validation import sha256_text

_TOKEN = re.compile(r"[A-Za-z0-9_]+|[\u3400-\u9fff]")


@dataclass(frozen=True)
class TextChunk:
    ordinal: int
    char_start: int
    char_end: int
    content: str
    content_hash: str
    token_estimate: int


def chunk_snapshot(text: str, *, max_chars: int = 2400, overlap_chars: int = 240) -> list[TextChunk]:
    if max_chars < 200 or overlap_chars < 0 or overlap_chars >= max_chars:
        raise ValueError("Invalid chunk size or overlap")
    chunks: list[TextChunk] = []
    start = 0
    while start < len(text):
        hard_end = min(len(text), start + max_chars)
        end = hard_end
        if hard_end < len(text):
            candidates = [text.rfind(separator, start + max_chars // 2, hard_end) for separator in ("\n\n", "\n", "。", ". ")]
            boundary = max(candidates)
            if boundary > start:
                end = boundary + 1
        content = text[start:end].strip()
        if content:
            raw_start = text.find(content, start, end)
            raw_end = raw_start + len(content)
            chunks.append(
                TextChunk(
                    ordinal=len(chunks),
                    char_start=raw_start,
                    char_end=raw_end,
                    content=content,
                    content_hash=sha256_text(content),
                    token_estimate=max(1, math.ceil(len(content) / 3)),
                )
            )
        if end >= len(text):
            break
        start = max(start + 1, end - overlap_chars)
    return chunks


def rank_chunks(
    query: str,
    chunks: list[dict[str, Any]],
    *,
    limit: int = 12,
    query_embedding: list[float] | None = None,
) -> list[dict[str, Any]]:
    """Deterministic hybrid BM25 + hashed n-gram embedding ranker."""

    query_terms = _terms(query)
    if not query_terms or not chunks:
        return chunks[:limit]
    documents = [_terms(str(chunk.get("content") or "")) for chunk in chunks]
    query_vector = query_embedding or hashed_embedding(query)
    average_length = sum(len(document) for document in documents) / max(1, len(documents))
    document_frequency = {term: sum(term in document for document in documents) for term in query_terms}
    scored: list[tuple[float, int, dict[str, Any]]] = []
    for index, (chunk, document) in enumerate(zip(chunks, documents, strict=True)):
        score = 0.0
        for term in query_terms:
            frequency = document.count(term)
            if not frequency:
                continue
            inverse = math.log(1 + (len(documents) - document_frequency[term] + 0.5) / (document_frequency[term] + 0.5))
            denominator = frequency + 1.2 * (0.25 + 0.75 * len(document) / max(1.0, average_length))
            score += inverse * frequency * 2.2 / denominator
        embedding = chunk.get("embedding")
        if not isinstance(embedding, list) or len(embedding) != len(query_vector):
            embedding = hashed_embedding(str(chunk.get("content") or ""))
        semantic_score = cosine_similarity(query_vector, embedding) if len(embedding) == len(query_vector) else 0.0
        hybrid_score = score + 0.35 * semantic_score
        scored.append((hybrid_score, -index, chunk))
    scored.sort(reverse=True, key=lambda item: (item[0], item[1]))
    return [{**chunk, "retrieval_score": score} for score, _, chunk in scored[:limit] if score > 0]


def hashed_embedding(value: str, *, dimensions: int = 128) -> list[float]:
    normalized = "".join(value.casefold().split())
    features = _terms(value) + [normalized[index : index + 3] for index in range(max(0, len(normalized) - 2))]
    vector = [0.0] * dimensions
    for feature in features:
        digest = blake2b(feature.encode("utf-8"), digest_size=8).digest()
        slot = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[slot] += sign
    magnitude = math.sqrt(sum(value * value for value in vector))
    return [value / magnitude for value in vector] if magnitude else vector


def cosine_similarity(left: list[float], right: list[float]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True))


def _terms(value: str) -> list[str]:
    return [token.casefold() for token in _TOKEN.findall(value)]
