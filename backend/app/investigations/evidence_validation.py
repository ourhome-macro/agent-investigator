from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass


class EvidenceValidationError(ValueError):
    pass


@dataclass(frozen=True)
class QuoteMatch:
    quote: str
    start: int
    end: int
    snapshot_sha256: str


_NUMBER = re.compile(r"(?<![\w.])\d+(?:[,.]\d+)*(?:%|％)?(?![\w.])")
_CURRENCY = re.compile(r"(?:US\$|USD|CNY|RMB|￥|¥|\$|€|£)\s*\d+(?:[,.]\d+)*", re.IGNORECASE)
_DATE = re.compile(r"(?:20\d{2}[-/.年]\d{1,2}(?:[-/.月]\d{1,2}日?)?|20\d{2}年)")


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def locate_verbatim_quote(snapshot: str, quote: str) -> QuoteMatch:
    """Locate a quote in an immutable snapshot, allowing only text normalization."""

    if not snapshot or not quote.strip():
        raise EvidenceValidationError("Evidence quote and snapshot must not be empty")
    direct = snapshot.find(quote)
    if direct >= 0:
        return QuoteMatch(quote=snapshot[direct : direct + len(quote)], start=direct, end=direct + len(quote), snapshot_sha256=sha256_text(snapshot))

    normalized_snapshot, positions = _normalize_with_positions(snapshot)
    normalized_quote, _ = _normalize_with_positions(quote)
    index = normalized_snapshot.find(normalized_quote)
    if index < 0:
        raise EvidenceValidationError("verbatim_quote is not a substring of the immutable Evidence snapshot")
    start = positions[index]
    end = positions[index + len(normalized_quote) - 1] + 1
    return QuoteMatch(quote=snapshot[start:end], start=start, end=end, snapshot_sha256=sha256_text(snapshot))


def require_claim_values_in_quotes(claim_text: str, quotes: list[str]) -> None:
    """Reject numeric/date/price values that do not occur in supporting quotes."""

    source = _comparison_text("\n".join(quotes))
    missing = [token for token in _exact_tokens(claim_text) if _comparison_text(token) not in source]
    if missing:
        raise EvidenceValidationError("Claim contains exact values absent from supporting quotes: " + ", ".join(sorted(set(missing))))


def require_price_fields_in_quote(*, amount: str, currency: str, billing_period: str, quote: str) -> None:
    normalized = _comparison_text(quote)
    amount_token = re.sub(r"(?<=\d)[,_](?=\d)", "", _comparison_text(amount))
    numeric_quote = re.sub(r"(?<=\d)[,_](?=\d)", "", normalized)
    if amount_token not in numeric_quote:
        raise EvidenceValidationError(f"Price amount {amount!r} is absent from the quote")
    currency_aliases = {
        "CNY": ("cny", "rmb", "￥", "¥", "人民币"),
        "USD": ("usd", "us$", "$", "美元"),
        "EUR": ("eur", "€", "欧元"),
        "GBP": ("gbp", "£", "英镑"),
    }.get(currency.upper(), (currency.casefold(),))
    if not any(_comparison_text(alias) in normalized for alias in currency_aliases):
        raise EvidenceValidationError(f"Price currency {currency!r} is absent from the quote")
    period_aliases = {
        "month": ("month", "monthly", "/mo", "每月", "月付", "个月"),
        "year": ("year", "yearly", "annual", "/yr", "每年", "年付"),
        "one_time": ("one-time", "one time", "一次性", "买断"),
        "usage": ("usage", "per request", "按量", "每次", "每百万"),
    }.get(billing_period, (billing_period,))
    if not any(_comparison_text(alias) in normalized for alias in period_aliases):
        raise EvidenceValidationError(f"Billing period {billing_period!r} is absent from the quote")


def _exact_tokens(value: str) -> list[str]:
    tokens = _CURRENCY.findall(value) + _DATE.findall(value) + _NUMBER.findall(value)
    result: list[str] = []
    for token in tokens:
        if token not in result:
            result.append(token)
    return result


def _comparison_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def _normalize_with_positions(value: str) -> tuple[str, list[int]]:
    output: list[str] = []
    positions: list[int] = []
    pending_space = False
    pending_position = 0
    for index, character in enumerate(value):
        normalized = unicodedata.normalize("NFKC", character)
        for item in normalized:
            if item.isspace():
                if output:
                    pending_space = True
                    pending_position = index
                continue
            if pending_space:
                output.append(" ")
                positions.append(pending_position)
                pending_space = False
            output.append(item.casefold())
            positions.append(index)
    return "".join(output), positions
