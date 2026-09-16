"""Source-aware evidence requirements, attribution and blocking policy."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from app.investigations.scoring import independent_source_count

POLICY_VERSION = "source-aware-v1"
SHARED_HOSTS = {"github.com", "gitlab.com", "gitee.com", "sourceforge.net", "reddit.com"}
_BLOCKING_RULES = {
    "semantic_support",
    "semantic_entailment",
    "contradiction",
    "exact_number",
    "exact_value",
    "pricing_source",
    "pricing_official_source",
    "atomicity",
    "source_independence",
    "factual_claim_requires_evidence",
}


def is_official_source(url: str, official_domains: list[str], official_repositories: list[str]) -> bool:
    parsed = urlsplit(url)
    host = (parsed.hostname or "").casefold()
    if parsed.scheme not in {"https", "http"} or parsed.username or parsed.password:
        return False
    if host.split(".")[0] in {"community", "forum", "forums", "discuss", "discussions"}:
        return False
    segments = [part.casefold() for part in parsed.path.split("/") if part]
    if segments and segments[0] in {"user", "users", "u", "video", "videos", "read", "opus", "space", "thread", "threads", "topic", "topics"}:
        return False
    if any(part in {"issues", "discussions", "forum", "forums", "community", "comments", "pull", "pulls"} for part in segments):
        return False
    if host in SHARED_HOSTS:
        for repository in official_repositories:
            root = urlsplit(repository)
            root_path = root.path.rstrip("/")
            source_path = parsed.path
            if host == "github.com":
                root_path, source_path = root_path.casefold(), source_path.casefold()
            if root.hostname == host and (source_path == root_path or source_path.startswith(root_path + "/")):
                return True
        return False
    return any(host == domain.casefold() or host.endswith("." + domain.casefold()) for domain in official_domains if domain)


def assess_support(claim_type: str, text: str, sources: list[dict]) -> str:
    """Sources must already have verified quotes and an entails verdict.

    Source attribution cannot be upgraded by adding more publications that
    repeat the same vendor assertion or individual user report.
    """
    if not sources:
        return "unverified"
    if claim_type == "user_report":
        return "user_reported"
    official = any(source["official"] for source in sources)
    if claim_type == "vendor_statement":
        return "vendor_stated" if official else "unverified"
    if independent_source_count(source["domain"] for source in sources) >= 2 and not all(source["official"] for source in sources):
        return "corroborated"
    if not official:
        return "unverified"
    # A single official page proves what the vendor says, not empirical effects.
    empirical = re.search(r"faster|slower|twice|best|benchmark|performance|market share|most popular|比.+更|最快|最好|性能|跑分|市场份额|领先", text, re.IGNORECASE)
    return "vendor_stated" if empirical else "official_documented"


def issue_is_blocking(issue: dict) -> bool:
    if issue.get("status", "open") != "open":
        return False
    return str(issue.get("severity", "warning")).lower() in {"error", "blocker", "critical"} or str(issue.get("rule", "")).lower().replace("-", "_") in _BLOCKING_RULES


def issue_blocks_claim(issue: dict, claim_id: str) -> bool:
    if not issue_is_blocking(issue):
        return False
    return issue.get("claim_id") == claim_id or (issue.get("claim_id") is None and issue.get("section_id") is None)


def claim_display_text(claim: dict) -> str:
    prefix = {"official_documented": "官方资料列示：", "vendor_stated": "厂商自述（未经独立验证）：", "user_reported": "个别来源反馈（不代表普遍现象）："}.get(claim.get("support_basis"), "")
    return prefix + claim["text"]


def issue_summary(issue: dict) -> str:
    reason = str(issue.get("reason", ""))
    if re.search(r"[\u3400-\u9fff]", reason):
        return reason
    key = str(issue.get("rule", "")).replace("-", "_")
    return {
        "semantic_support": "原文不足以支持这条结论。",
        "semantic_entailment": "结论与原文的支持关系仍需核对。",
        "atomicity": "这条结论需要拆分或缩小范围。",
        "contradiction": "资料之间存在尚未解释的冲突。",
        "independent_source_threshold": "尚缺足够可靠的直接来源。",
        "source_independence": "资料来源是否独立仍需确认。",
        "pricing_official_source": "价格尚缺官方依据。",
        "exact_number": "结论中的数字仍需核实。",
        "factual_claim_requires_evidence": "结论尚无直接资料支持。",
    }.get(key, "还有一项信息需要补充确认，可在工作台查看详情。")


def completion_state(investigation: dict, cells: list[dict], claims: list[dict], issues: list[dict]) -> str:
    factual = [claim for claim in claims if claim.get("support_basis") not in {"user_reported", "vendor_stated"}]
    factual_ids = {claim["id"] for claim in factual}
    covered_competitors = {cell["competitor_id"] for cell in cells if set(cell["claim_ids"]) & factual_ids}
    required_dimensions = set(investigation["scope"].get("required_dimensions", []))
    interrupted = investigation.get("status") in {"failed", "cancelled", "cancelling"}
    report_blocked = any(issue_is_blocking(issue) and issue.get("claim_id") is None for issue in issues)
    missing_core = any(cell["competitor_id"] not in covered_competitors or (cell["dimension"] in required_dimensions and cell["status"] != "covered") for cell in cells)
    if interrupted or report_blocked or missing_core or not factual or investigation.get("annotation_unresolved"):
        return "incomplete"
    if any(cell["status"] != "covered" for cell in cells) or any(issue.get("status", "open") == "open" for issue in issues):
        return "completed_with_gaps"
    return "completed"
