from __future__ import annotations

import pytest

from app.investigations.evidence_validation import (
    EvidenceValidationError,
    locate_verbatim_quote,
    require_claim_values_in_quotes,
    require_price_fields_in_quote,
    sha256_text,
)


def test_quote_must_be_present_and_returns_raw_offsets() -> None:
    snapshot = "专业版\n每年收费 ￥1,999，包含 20 个席位。"
    match = locate_verbatim_quote(snapshot, "专业版 每年收费 ¥1,999，包含 20 个席位。")
    assert snapshot[match.start : match.end] == match.quote
    assert match.snapshot_sha256 == sha256_text(snapshot)
    with pytest.raises(EvidenceValidationError, match="not a substring"):
        locate_verbatim_quote(snapshot, "企业版每年收费 ￥1,999")


def test_claim_exact_values_must_appear_in_supporting_quotes() -> None:
    require_claim_values_in_quotes("专业版年费为 ￥1,999，包含 20 个席位。", ["每年收费 ￥1,999，包含 20 个席位。"])
    with pytest.raises(EvidenceValidationError, match="30"):
        require_claim_values_in_quotes("专业版年费为 ￥1,999，包含 30 个席位。", ["每年收费 ￥1,999，包含 20 个席位。"])


def test_price_observation_requires_amount_currency_and_period() -> None:
    quote = "Pro 版本按年付费，每年人民币 ¥1,999。"
    require_price_fields_in_quote(amount="1999", currency="CNY", billing_period="year", quote=quote)
    with pytest.raises(EvidenceValidationError, match="month"):
        require_price_fields_in_quote(amount="1999", currency="CNY", billing_period="month", quote=quote)
