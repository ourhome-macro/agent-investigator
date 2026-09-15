from __future__ import annotations

from typing import Any

from app.investigations.confidence import POLICY_VERSION, claim_display_text, issue_summary
from app.investigations.quality import claim_is_eligible


def build_partial_report(
    investigation: dict[str, Any],
    claims: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    issues: list[dict[str, Any]],
) -> tuple[dict[str, Any], str]:
    # Failed research is a status receipt, not eleven duplicated report chapters.
    verified = [claim for claim in claims if claim_is_eligible(claim, issues)]
    pending = [claim for claim in claims if not claim_is_eligible(claim, issues) and claim.get("status") not in {"rejected", "superseded"}]
    definitions = (
        ("executive_summary", "研究进展概览"),
        ("methodology", "研究范围与方法"),
        ("verified_findings", "可引用的发现"),
        ("risks_unknowns", "风险、冲突证据和未知项"),
        ("evidence_appendix", "资料来源"),
    )
    sections: list[dict[str, Any]] = []
    all_claim_ids = [claim["id"] for claim in claims]
    all_evidence_ids = [item["id"] for item in evidence]
    for section_type, title in definitions:
        lines = [f"## {title}", ""]
        section_claims = verified if section_type == "verified_findings" else []
        if section_type == "methodology":
            lines.extend(
                [
                    "本报告由失败时可用的结构化数据生成，未完成的语义审计不会被视为已验证。",
                    f"市场：{investigation['scope']['market']}；时间范围：{investigation['scope']['time_range']}。",
                    f"竞品：{'、'.join(investigation['scope']['competitors'])}。",
                ]
            )
            section_claims = []
        elif section_type == "executive_summary":
            lines.append(f"研究尚未完成，已在额度、资料核对或服务异常时停止。现有 {len(evidence)} 份资料、{len(verified)} 条注明来源的可引用结论、{len(pending)} 条待核实结论。以下仅为已有结果。")
            section_claims = []
        elif section_type == "risks_unknowns":
            section_claims = [{**claim, "status": "uncertain"} for claim in pending]
            lines.extend(f"- 待处理问题：{issue_summary(issue)}" for issue in issues if issue.get("status", "open") == "open")
        elif section_type == "evidence_appendix":
            section_claims = []
            for item in evidence:
                lines.append(f"- {item['title']} — {item['source_domain']} — {item['source_url']}")
        if section_claims:
            for claim in section_claims:
                label = "待核实" if claim["status"] != "supported" else "可引用"
                lines.append(f"- [{label}] {claim['dimension']}：{claim_display_text(claim)}（来源编号：{', '.join(claim['evidence_ids']) or '暂无'}）")
        elif len(lines) == 2:
            lines.append("当前结构化证据不足，未形成可审计结论。")
        sections.append(
            {
                "id": section_type,
                "type": section_type,
                "title": title,
                "markdown": "\n".join(lines),
                "claim_ids": [claim["id"] for claim in section_claims],
                "evidence_ids": all_evidence_ids if section_type == "evidence_appendix" else sorted({evidence_id for claim in section_claims for evidence_id in claim["evidence_ids"]}),
            }
        )
    markdown = f"# {investigation['title']}\n\n" + "\n\n".join(section["markdown"] for section in sections) + "\n"
    return {
        "schema_version": "competitive-report-v2-partial",
        "title": investigation["title"],
        "partial": True,
        "quality_policy_version": POLICY_VERSION,
        "completion_status": "incomplete",
        "claim_ids": all_claim_ids,
        "claim_versions": {claim["id"]: claim.get("version", 1) for claim in verified},
        "claim_support_basis": {claim["id"]: claim.get("support_basis", "corroborated") for claim in verified},
        "sections": sections,
    }, markdown
