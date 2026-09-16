"""Versioned research resources and decision presentation (not truth thresholds)."""

from __future__ import annotations

from copy import deepcopy

from app.investigations.confidence import POLICY_VERSION

PERSPECTIVES = {
    "product": {"label": "产品规划", "section_title": "产品优先级与验证计划", "question": "哪些功能值得先做，依据是什么，下一步怎样验证？"},
    "purchase": {"label": "采购选型", "section_title": "采购选择与核对清单", "question": "哪些选择符合需求，成本和限制是什么，采购前还要核实什么？"},
    "sales": {"label": "销售对比", "section_title": "销售对比与沟通要点", "question": "有哪些有依据的差异，适合哪些客户，哪些承诺尚不能作出？"},
    "operations": {"label": "运营与用户研究", "section_title": "用户机会与运营验证", "question": "有哪些用户需求和使用阻力，哪些运营假设值得验证？"},
}

_MODES = {
    "quick": {
        "label": "快速了解",
        "description": "先回答主要问题，资料量较少，不自动追加返工。",
        "base_tokens": 180_000,
        "extra_tokens": 30_000,
        "minutes": 15,
        "search_results": 2,
        "candidate_limit": 8,
        "max_rework_rounds": 0,
        "collector_tokens": 25_000,
        "analysis_tokens": 25_000,
        "audit_tokens": 35_000,
        "report_tokens": 25_000,
        "refinement_tokens": 100_000,
    },
    "standard": {
        "label": "常规研究",
        "description": "兼顾资料覆盖与用量，核心缺口可自动补充一轮。",
        "base_tokens": 300_000,
        "extra_tokens": 75_000,
        "minutes": 30,
        "search_results": 4,
        "candidate_limit": 12,
        "max_rework_rounds": 1,
        "collector_tokens": 35_000,
        "analysis_tokens": 30_000,
        "audit_tokens": 35_000,
        "report_tokens": 35_000,
        "refinement_tokens": 150_000,
    },
    "deep": {
        "label": "深入研究",
        "description": "增加采集范围和执行额度，核心缺口最多补充两轮。",
        "base_tokens": 450_000,
        "extra_tokens": 100_000,
        "minutes": 45,
        "search_results": 6,
        "candidate_limit": 18,
        "max_rework_rounds": 2,
        "collector_tokens": 45_000,
        "analysis_tokens": 40_000,
        "audit_tokens": 45_000,
        "report_tokens": 45_000,
        "refinement_tokens": 220_000,
    },
}


def selected_policy(mode: str, competitors: int) -> dict:
    if mode not in _MODES or not 2 <= competitors <= 5:
        raise ValueError("Unknown research mode or competitor count")
    config = deepcopy(_MODES[mode])
    return {
        **config,
        "mode": mode,
        "version": "research-resources-v1",
        "confidence_policy": POLICY_VERSION,
        "token_budget": config["base_tokens"] + max(0, competitors - 2) * config["extra_tokens"],
        "max_annotations": 3,
        "refinement_minutes": 15,
    }


def policy_for(investigation: dict) -> dict:
    if investigation.get("policy_snapshot"):
        return deepcopy(investigation["policy_snapshot"])
    # Existing runs were admitted under the previous two-round policy.
    count = len(investigation.get("scope", {}).get("competitors", []))
    policy = selected_policy("standard", count if 2 <= count <= 5 else 2)
    return {**policy, "token_budget": investigation.get("token_budget", policy["token_budget"]), "version": "legacy-ci-v2", "max_rework_rounds": 2}


def execution_cap(investigation: dict, stage: str, *, single_run: bool = False) -> int:
    policy = policy_for(investigation)
    key = "report_tokens" if stage == "synthesizing" else "analysis_tokens" if stage == "analyzing" else "audit_tokens" if stage in {"auditing", "planning"} or single_run else "collector_tokens"
    return policy[key]


def research_options() -> dict:
    return {"modes": [{"id": name, **selected_policy(name, 2)} for name in _MODES], "perspectives": [{"id": name, **value} for name, value in PERSPECTIVES.items()], "confidence_policy": POLICY_VERSION}


def decision_context(scope: dict) -> dict:
    perspective = scope.get("perspective", "product")
    if perspective not in PERSPECTIVES:
        perspective = "product"
    return {"perspective": perspective, "goal": scope.get("decision_goal", ""), **PERSPECTIVES[perspective]}
