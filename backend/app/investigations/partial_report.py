from __future__ import annotations

from typing import Any


def build_partial_report(
    investigation: dict[str, Any],
    claims: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    issues: list[dict[str, Any]],
) -> tuple[dict[str, Any], str]:
    definitions = (
        ("executive_summary", "Executive Summary"),
        ("methodology", "研究范围与方法"),
        ("landscape", "赛道和竞品定义"),
        ("competitor_profiles", "竞品画像"),
        ("feature_matrix", "功能能力矩阵"),
        ("pricing", "定价与商业模式"),
        ("positioning", "用户与市场定位"),
        ("strengths_weaknesses", "优势、短板和竞争壁垒"),
        ("opportunities", "差异化机会"),
        ("risks_unknowns", "风险、冲突证据和未知项"),
        ("evidence_appendix", "Evidence Appendix"),
    )
    sections: list[dict[str, Any]] = []
    all_claim_ids = [claim["id"] for claim in claims]
    all_evidence_ids = [item["id"] for item in evidence]
    for section_type, title in definitions:
        lines = [f"## {title}", ""]
        section_claims = claims
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
            supported = sum(claim["status"] == "supported" for claim in claims)
            lines.append(f"执行在预算或技术闸门处停止。现有 {len(evidence)} 条 Evidence、{len(claims)} 条 Claim，其中 {supported} 条已支持；其余必须视为 uncertain。")
            section_claims = []
        elif section_type == "risks_unknowns":
            section_claims = [claim for claim in claims if claim["status"] != "supported"]
            lines.extend(f"- Audit Issue：{issue['rule']} — {issue['reason']}" for issue in issues)
        elif section_type == "evidence_appendix":
            section_claims = []
            for item in evidence:
                lines.append(f"- [{item['id']}] {item['title']} — {item['source_domain']} — 可信度 {item['credibility_score']}/100 — {item['source_url']}")
        if section_claims:
            for claim in section_claims:
                lines.append(f"- [{claim['status']}] {claim['dimension']}：{claim['text']} (Evidence: {', '.join(claim['evidence_ids']) or 'none'})")
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
        "schema_version": "competitive-report-v1-partial",
        "title": investigation["title"],
        "partial": True,
        "claim_ids": all_claim_ids,
        "sections": sections,
    }, markdown
