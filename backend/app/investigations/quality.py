"""Research admission, coverage and publication policies. No model-owned state."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.investigations.confidence import POLICY_VERSION, claim_display_text, completion_state, is_official_source, issue_blocks_claim, issue_summary
from app.investigations.product import decision_context
from app.investigations.providers import canonicalize_url


@dataclass(frozen=True)
class AdmissionDecision:
    accepted: bool
    reason: str
    source_tier: str = "unverified"


_DIMENSION_TERMS = {
    "播放": ("playback", "player", "video", "播放", "视频"),
    "弹幕": ("danmaku", "danmu", "弹幕"),
    "跨设备": ("sync", "device", "platform", "android", "ios", "windows", "同步", "设备", "多端"),
    "开源": ("open source", "opensource", "license", "gpl", "github", "开源", "许可"),
    "维护": ("maintain", "release", "issue", "version", "更新", "维护", "版本"),
    "扩展": ("plugin", "extension", "api", "插件", "扩展"),
    "账号": ("account", "login", "登录", "账号", "账户"),
    "功能": ("feature", "support", "支持", "功能", "capabilit"),
    "离线": ("offline", "download", "离线", "下载", "缓存"),
    "投屏": ("cast", "dlna", "airplay", "投屏"),
    "登录": ("login", "log in", "sign in", "登录", "登陆", "账号"),
    "定价": ("price", "pricing", "billing", "free", "定价", "付费", "免费", "套餐"),
    "商业化": ("pricing", "subscription", "license", "commercial", "广告", "商业", "付费", "免费"),
    "合规": ("license", "copyright", "terms", "permission", "版权", "授权", "许可", "合规"),
    "定位": ("audience", "position", "designed", "面向", "定位", "用户"),
    "用户": ("user", "customer", "用户", "客户"),
    "壁垒": ("architecture", "patent", "technology", "技术", "专利", "架构", "壁垒"),
}


def _contains(text: str, term: str) -> bool:
    if not term.strip():
        return False
    if re.fullmatch(r"[a-zA-Z0-9 ._-]+", term):
        return re.search(r"(?<!\w)" + re.escape(term.casefold()) + r"(?!\w)", text.casefold()) is not None
    return term.casefold() in text.casefold()


def candidate_excerpt(content: str, dimension: str, *, limit: int = 1200) -> str:
    terms = dimension_terms(dimension)
    positions = [content.casefold().find(term.casefold()) for term in terms]
    position = min((value for value in positions if value >= 0), default=0)
    start = max(0, position - 250)
    return content[start : start + limit].strip()


def dimension_terms(dimension: str) -> tuple[str, ...]:
    english = {"features": "功能", "pricing": "定价", "positioning": "定位", "users": "用户", "offline": "离线", "casting": "投屏", "login": "登录", "compliance": "合规", "maintenance": "维护"}
    dimension = english.get(dimension.casefold(), dimension)
    matched = [term for key, terms in _DIMENSION_TERMS.items() if key in dimension for term in terms]
    return tuple(dict.fromkeys(matched)) if matched else tuple(part.strip() for part in re.split(r"[,/、；;]|\band\b", dimension) if part.strip())


def entity_names(name: str) -> list[str]:
    shortened = re.sub(r"(?:官方客户端|客户端|播放器|\s+official client|\s+app)$", "", name, flags=re.IGNORECASE).strip()
    return [name, shortened] if len(shortened) >= 3 else [name]


def balance_candidates(hits: list[dict], dimensions: list[str], limit: int = 12) -> list[dict]:
    buckets = [[hit for hit in hits if hit["dimension"] == dimension] for dimension in dimensions]
    result = []
    for index in range(max((len(bucket) for bucket in buckets), default=0)):
        for bucket in buckets:
            if index < len(bucket):
                result.append(bucket[index])
                if len(result) == limit:
                    return result
    return result


def admit_candidate(*, url: str, offered_urls: list[str], competitor: str, dimension: str, title: str, excerpt: str, official_domains: list[str], official_repositories: list[str] | None = None) -> AdmissionDecision:
    """Admission floor; semantic binding audit must still prove the claim."""
    canonical = canonicalize_url(url)
    if canonical not in {canonicalize_url(item) for item in offered_urls}:
        return AdmissionDecision(False, "unoffered_url")
    official = is_official_source(canonical, official_domains, official_repositories or [])
    if not official and not any(_contains(title + "\n" + excerpt, name) for name in entity_names(competitor)):
        return AdmissionDecision(False, "entity_not_established")
    terms = dimension_terms(dimension)
    if not any(term.casefold() in excerpt.casefold() for term in terms):
        return AdmissionDecision(False, "question_not_addressed")
    return AdmissionDecision(True, "entity_and_question_matched", "primary" if official else "secondary")


def claim_is_eligible(claim: dict[str, Any], issues: list[dict[str, Any]]) -> bool:
    if claim.get("status") != "supported" or claim.get("claim_type", "fact") not in {"fact", "pricing", "vendor_statement", "user_report"}:
        return False
    if claim.get("support_basis") in {"unverified", "legacy_unverified"}:
        return False
    return not any(issue_blocks_claim(issue, claim["id"]) for issue in issues)


def coverage_cells(competitors, dimensions, claims, issues):
    result = []
    for competitor in competitors:
        for dimension in dimensions:
            matching = [claim for claim in claims if claim.get("competitor_id") == competitor["id"] and claim["dimension"] == dimension and claim.get("status") not in {"superseded", "rejected"}]
            eligible = [claim for claim in matching if claim_is_eligible(claim, issues)]
            documented = [claim for claim in eligible if claim.get("support_basis") not in {"user_reported", "vendor_stated"}]
            result.append(
                {"competitor_id": competitor["id"], "competitor": competitor["name"], "dimension": dimension, "status": "covered" if documented else "partial" if matching else "missing", "claim_ids": [claim["id"] for claim in eligible]}
            )
    return result


REPORT_SECTIONS = (
    ("executive_summary", "研究结论"),
    ("methodology", "研究范围与方法"),
    ("landscape", "赛道和竞品定义"),
    ("competitor_profiles", "竞品画像"),
    ("feature_matrix", "能力与证据覆盖矩阵"),
    ("pricing", "定价与商业模式"),
    ("positioning", "用户与市场定位"),
    ("strengths_weaknesses", "优势、短板和竞争壁垒"),
    ("opportunities", "机会假设与验证动作"),
    ("risks_unknowns", "信息缺口与注意事项"),
    ("evidence_appendix", "证据索引"),
)


def _cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_grounded_report(investigation, sections, claims, evidence, issues, competitors):
    """The model routes IDs and proposes hypotheses; facts are rendered here."""
    eligible = {claim["id"]: claim for claim in claims if claim_is_eligible(claim, issues)}
    decision = decision_context(investigation["scope"])
    allowed_types = {kind for kind, _ in REPORT_SECTIONS}
    routes = {}
    suggestions = []
    seen_sections = set()
    for section in sections:
        kind = section.get("type")
        if kind not in allowed_types or kind in seen_sections:
            raise ValueError("Unknown or duplicate report section")
        seen_sections.add(kind)
        if str(section.get("markdown") or "").strip():
            raise ValueError("Report does not accept free-form factual Markdown; submit claim_ids and hypotheses")
        ids = section.get("claim_ids", [])
        if not isinstance(ids, list) or any(item not in eligible for item in ids):
            raise ValueError("Report references an ineligible or unknown Claim")
        routes[kind] = list(dict.fromkeys(ids))
        for proposal in section.get("hypotheses", []):
            premises = proposal.get("premise_claim_ids", [])
            if not premises or any(item not in eligible for item in premises):
                raise ValueError("Opportunity hypothesis requires eligible premises")
            if not proposal.get("hypothesis") or not proposal.get("validation"):
                raise ValueError("Opportunity hypothesis requires a validation action")
            suggestions.append(proposal)
    cells = coverage_cells(competitors, investigation["scope"]["dimensions"], claims, issues)
    missing = [cell for cell in cells if cell["status"] != "covered"]
    open_issues = [issue for issue in issues if issue.get("status", "open") == "open"]
    completion = completion_state(investigation, cells, list(eligible.values()), issues)
    partial = completion == "incomplete"
    names = {item["id"]: item["name"] for item in competitors}
    claim_labels = {cid: f"结论 {index + 1}" for index, cid in enumerate(eligible)}
    evidence_labels = {item["id"]: f"资料 {index + 1}" for index, item in enumerate(evidence)}
    for cid, claim in eligible.items():
        if any(cid in ids for kind, ids in routes.items() if kind in {"competitor_profiles", "pricing", "positioning", "strengths_weaknesses"}):
            continue
        dimension = claim["dimension"].casefold()
        kind = "pricing" if claim.get("claim_type") == "pricing" else "positioning" if dimension in {"定位", "用户", "positioning", "users"} else "strengths_weaknesses" if dimension in {"壁垒", "barriers"} else "competitor_profiles"
        routes.setdefault(kind, []).append(cid)
    result = []
    used = set()
    cited = {eid for claim in eligible.values() for eid in claim.get("evidence_ids", [])}
    for kind, title in REPORT_SECTIONS:
        body = []
        ids = []
        if kind == "executive_summary":
            status_label = {"incomplete": "未完成", "completed": "已完成", "completed_with_gaps": "已完成，包含已知缺口"}[completion]
            body = [f"研究状态：{status_label}。可引用 {len(eligible)} 条结论，来源等级分别标注；{len(cells) - len(missing)}/{len(cells)} 个研究单元有产品事实依据。"]
            body.append(f"决策视角：{decision['label']}。本次要决定：{decision['goal'] or decision['question']}")
            if investigation.get("annotation"):
                request = investigation["annotation"]
                body.append(f"本轮针对第 {request['payload']['report_version']} 版的批注补研：{request['payload']['comment']}。处理结果：{'仍有问题需要确认' if investigation.get('annotation_unresolved') else '已重新核对并生成新版本'}。")
            ids = list(routes.get(kind, [])[:3])
            body.extend(f"- 关键发现【{claim_labels[cid]}】：{claim_display_text(eligible[cid])}" for cid in routes.get(kind, [])[:3])
            if suggestions:
                body.append(f"建议验证的方向（假设）：{suggestions[0]['hypothesis']}；下一步：{suggestions[0]['validation']}")
        elif kind == "methodology":
            body = [f"市场：{investigation['scope']['market']}；时间范围：{investigation['scope']['time_range']}。", "结论均附有可核对的原文。官方说明、厂商自述和用户反馈分别标注；缺少资料不代表产品没有这项能力。"]
        elif kind == "landscape":
            body = ["研究对象：" + "、".join(investigation["scope"]["competitors"])]
        elif kind == "feature_matrix":
            ids = list(dict.fromkeys(cid for cell in cells for cid in cell["claim_ids"]))
            body = ["| 竞品 | 比较项目 | 资料情况 | 结论与来源说明 | 对应结论 |", "|---|---|---|---|---|"]
            for cell in cells:
                finding = _cell("；".join(claim_display_text(eligible[cid]) for cid in cell["claim_ids"])) or "未知"
                state = "有产品事实依据" if cell["status"] == "covered" else "仍需核实"
                body.append(f"| {_cell(cell['competitor'])} | {_cell(cell['dimension'])} | {state} | {finding} | {'、'.join(claim_labels[cid] for cid in cell['claim_ids']) or '暂无'} |")
        elif kind == "risks_unknowns":
            body = [f"- {cell['competitor']} / {cell['dimension']}：证据尚不足。" for cell in missing]
            body.extend(f"- 待处理或补充说明：{issue_summary(issue)}" for issue in open_issues)
        elif kind == "evidence_appendix":
            body = [f"- 【{evidence_labels[item['id']]}】{item['title']} — {item['source_url']}" for item in evidence if item["id"] in cited]
        elif kind == "opportunities":
            title = decision["section_title"]
            ids = list(dict.fromkeys(cid for proposal in suggestions for cid in proposal["premise_claim_ids"]))
            body = [f"- **待验证假设**：{item['hypothesis']}\n  前提：{'、'.join(claim_labels[cid] for cid in item['premise_claim_ids'])}；验证动作：{item['validation']}" for item in suggestions]
            body.insert(0, f"本节回答：{decision['question']}")
            if not suggestions:
                body.append("本次未发现有充分依据的机会建议。")
        else:
            for cid in routes.get(kind, []):
                if cid in used:
                    continue
                claim = eligible[cid]
                body.append(f"- {names.get(claim.get('competitor_id'), '')}【{claim_labels[cid]}】{claim_display_text(claim)}（依据：{'、'.join(evidence_labels.get(eid, '资料待补充') for eid in claim.get('evidence_ids', []))}）")
                ids.append(cid)
                used.add(cid)
        result.append(
            {
                "id": kind,
                "type": kind,
                "title": title,
                "markdown": ("\n" if kind == "feature_matrix" else "\n\n").join(body) or "当前没有可交付的已验证内容。",
                "claim_ids": ids,
                "evidence_ids": sorted({eid for cid in ids for eid in eligible[cid].get("evidence_ids", [])}),
            }
        )
    data = {
        "schema_version": "competitive-report-v2",
        "quality_policy_version": POLICY_VERSION,
        "completion_status": completion,
        "coverage_status": "has_gaps" if missing else "complete",
        "title": investigation["title"],
        "decision": decision,
        "refinement": {"request_id": investigation["annotation"]["id"], "source_report_version": investigation["annotation"]["payload"]["report_version"], "unresolved": bool(investigation.get("annotation_unresolved"))}
        if investigation.get("annotation")
        else None,
        "claim_snapshots": [
            {
                "id": claim["id"],
                "version": claim.get("version", 1),
                "competitor_id": claim.get("competitor_id"),
                "dimension": claim["dimension"],
                "text": claim["text"],
                "display_text": claim_display_text(claim),
                "support_basis": claim.get("support_basis"),
                "evidence_ids": claim.get("evidence_ids", []),
                "statement": claim.get("statement", {}),
            }
            for claim in eligible.values()
        ],
        "partial": partial,
        "sections": result,
        "coverage": cells,
        "claim_versions": {cid: claim.get("version", 1) for cid, claim in eligible.items()},
        "claim_support_basis": {cid: claim.get("support_basis", "corroborated") for cid, claim in eligible.items()},
    }
    return data, "# " + investigation["title"] + "\n\n" + "\n\n".join(f"## {section['title']}\n\n{section['markdown']}" for section in result)
