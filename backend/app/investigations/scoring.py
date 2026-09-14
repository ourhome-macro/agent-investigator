from __future__ import annotations

from collections.abc import Iterable


def credibility_score(*, source_authority: int, freshness: int, extraction_quality: int, specificity: int, corroboration: int) -> int:
    parts = ((source_authority, 30), (freshness, 20), (extraction_quality, 10), (specificity, 10), (corroboration, 30))
    if any(value < 0 or value > maximum for value, maximum in parts):
        raise ValueError("Evidence score component is out of range")
    return sum(value for value, _ in parts)


def independent_source_count(domains: Iterable[str]) -> int:
    return len({_registrable_domain(domain) for domain in domains if domain.strip()})


def _registrable_domain(domain: str) -> str:
    labels = domain.strip().lower().removeprefix("www.").rstrip(".").split(".")
    if len(labels) <= 2:
        return ".".join(labels)
    common_second_level = {"com.cn", "net.cn", "org.cn", "co.uk", "com.au", "co.jp"}
    suffix = ".".join(labels[-2:])
    return ".".join(labels[-3:]) if suffix in common_second_level else suffix


def claim_is_supported(*, material: bool, supporting_domains: Iterable[str]) -> bool:
    required = 2 if material else 1
    return independent_source_count(supporting_domains) >= required
