import type { AuditIssue, Claim, InvestigationStatus, Report } from "./types";

export function claimDisplayStatus(
  claim: Pick<Claim, "status" | "publication_eligible" | "support_basis">,
): string {
  if (claim.status === "superseded") return "已替代";
  if (claim.status === "rejected") return "已拒绝";
  if (claim.status === "contradicted") return "存在反证";
  if (claim.support_basis === "legacy_unverified") return "历史结论待复核";
  if (claim.status !== "supported" || claim.publication_eligible !== true)
    return "仍需核实";
  return {
    official_documented: "官方资料说明",
    vendor_stated: "厂商自述",
    user_reported: "个别用户反馈",
    corroborated: "多个来源支持",
    unverified: "仍需核实",
    legacy_unverified: "历史结论待复核",
  }[claim.support_basis ?? "corroborated"];
}

export function claimStatusExplanation(
  claim: Pick<Claim, "status" | "publication_eligible" | "support_basis">,
): string {
  if (claim.support_basis === "legacy_unverified")
    return "这条历史结论尚未按当前来源规则复核，暂不作为确定事实。";
  if (claim.status === "superseded")
    return "这条旧结论已被修订版本替代，不会作为最终依据。";
  if (claim.status === "rejected") return "这条结论已被排除，不会进入报告。";
  if (claim.status === "contradicted")
    return "发现了与这条结论相反的资料，需要核对版本、时间或适用条件。";
  if (!claim.publication_eligible)
    return "依据仍不足，或有影响可信性的问题尚未处理；暂不作为确定事实。";
  if (claim.support_basis === "official_documented")
    return "官方资料明确写出了这项信息，引用已核对；请留意对应版本和时间。";
  if (claim.support_basis === "vendor_stated")
    return "这是厂商或项目自己的说法，尚未获得独立实测验证。";
  if (claim.support_basis === "user_reported")
    return "这是具体用户的反馈，不能据此判断所有用户都会遇到同样问题。";
  return "多个来源的原文支持这条结论，尚未发现未解决的实质冲突。";
}

const stages: Record<string, string> = {
  draft: "尚未开始",
  planning: "正在制定研究计划",
  awaiting_scope_approval: "请确认研究范围",
  collecting: "正在收集资料",
  normalizing: "正在整理资料",
  analyzing: "正在提炼结论",
  auditing: "正在核对结论",
  reworking: "正在补充资料与修订",
  synthesizing: "正在整理报告",
  awaiting_publish_approval: "请确认报告",
  published: "报告已发布",
  cancelling: "正在停止",
  cancelled: "研究已停止",
  failed: "研究暂未完成",
};
export function researchStageLabel(status: string): string {
  return stages[status] ?? "正在处理";
}

export function researchActivityLabel(stage: string): string {
  return (
    (
      {
        planning: "制定研究计划",
        collecting: "收集资料",
        normalizing: "整理资料",
        analyzing: "提炼结论",
        auditing: "核对结论",
        reworking: "补充资料与修订",
        synthesizing: "整理报告",
      } as Record<string, string>
    )[stage] ?? "处理研究任务"
  );
}

export function researchNextStep(status: InvestigationStatus): string {
  if (status === "awaiting_scope_approval")
    return "请核对竞品、比较项目和官方来源。确认后才会开始收集资料。";
  if (status === "awaiting_publish_approval")
    return "请检查报告中的结论、来源和已知缺口，再决定发布或退回补充。";
  if (status === "failed")
    return "已收集的资料会保留。请查看中断原因，再选择继续研究或保存已有结果。";
  if (status === "published")
    return "可以阅读或下载报告；结论的来源等级和已知缺口会一并保留。";
  if (status === "cancelled" || status === "cancelling")
    return "研究已停止推进，已经收集的资料仍可查看。";
  return "研究正在进行。下方会更新完成进度、资料来源和需要进一步核实的问题。";
}

export function reportStatusLabel(report: Report): string {
  if (report.structured_data?.partial) return "研究未完成 · 已有结果";
  if (report.structured_data?.completion_status === "completed_with_gaps")
    return "研究完成，含已知缺口";
  return "研究报告";
}

export function executionLabel(status: string): string {
  return (
    (
      {
        pending: "等待开始",
        running: "进行中",
        succeeded: "已完成",
        failed: "本项未完成",
        rejected: "结果需要修正",
        cancelled: "已停止",
      } as Record<string, string>
    )[status] ?? "等待更新"
  );
}

export function bindingLabel(status: string | null): string {
  return (
    (
      {
        supports: "用于支持结论",
        contradicts: "存在相反依据",
        context: "背景资料",
        verified: "原文已核对",
        pending: "原文待核对",
        entails: "原文直接支持",
        partially_supports: "仅支持部分内容",
        unrelated: "与结论不相符",
        pending_audit: "支持关系待核对",
      } as Record<string, string>
    )[status ?? "pending_audit"] ?? "仍需核实"
  );
}

export function issueTitle(issue: AuditIssue): string {
  const key = issue.rule.replaceAll("-", "_");
  return (
    (
      {
        semantic_support: "原文不足以支持结论",
        semantic_entailment: "结论与原文仍需核对",
        independent_source_threshold: "需要更可靠的来源",
        source_independence: "来源是否独立仍需确认",
        contradiction: "资料之间存在冲突",
        exact_number: "数字需要核实",
        pricing_source: "价格来源需要核实",
        pricing_official_source: "价格缺少官方依据",
        atomicity: "结论需要拆分或缩小范围",
        factual_claim_requires_evidence: "结论缺少直接依据",
        overgeneralization: "结论概括得过宽",
      } as Record<string, string>
    )[key] ?? (issue.blocking ? "有一项问题需要先处理" : "有一项补充说明")
  );
}

export function issueExplanation(issue: AuditIssue): string {
  return /[\u3400-\u9fff]/.test(issue.reason)
    ? issue.reason
    : `${issueTitle(issue)}。相关原始记录可在技术详情中查看。`;
}

export function issueAction(issue: AuditIssue): string {
  if (/[\u3400-\u9fff]/.test(issue.required_action))
    return issue.required_action;
  return (
    (
      {
        recollect: "查找更直接的资料，再核对这条结论。",
        revise: "把结论改写为原文能够支持的范围。",
        split: "将多个判断拆开，分别寻找依据。",
        reject: "从最终报告中排除这条结论。",
        investigate_conflict: "比较资料的时间、版本和条件，查明冲突原因。",
      } as Record<string, string>
    )[issue.required_action] ?? "查看相关资料，确认是否需要补充或修改结论。"
  );
}

export function readableError(error: string): string {
  if (/budget|token|额度|预算/i.test(error))
    return "本次分析额度不足。可以先保存已有结果，或缩小研究范围后重新发起。";
  if (/timeout|deadline|超时/i.test(error))
    return "本项处理时间过长，已停止等待。可以先保存已有结果，再检查服务后重试。";
  if (/provider|credential|api.key|durable|503/i.test(error))
    return "研究服务尚未准备好，请检查搜索、模型和任务服务配置后重试。";
  if (/eligibility|attribution|version changed|409/i.test(error))
    return "结论或报告状态已有变化，请刷新并重新核对后操作。";
  if (/401|403|unauthorized|authentication/i.test(error))
    return "当前账号无法执行此操作，请重新登录或确认访问权限。";
  if (/quote|snapshot|evidence|source.*valid/i.test(error))
    return "引用与原始资料尚未核对通过，需要补充可靠原文或修正相关结论。";
  if (/submission|json|schema|format/i.test(error))
    return "本次返回的内容格式不完整，需要重新生成这一阶段的结果。";
  if (/[\u3400-\u9fff]/.test(error)) return error;
  return "操作暂时未完成。请稍后重试；如仍失败，可查看技术详情排查。";
}

export function billingLabel(value: string): string {
  return (
    (
      {
        month: "每月",
        year: "每年",
        one_time: "一次性",
        usage: "按用量",
        account: "每个账号",
        seat: "每个席位",
        user: "每位用户",
      } as Record<string, string>
    )[value] ?? value
  );
}

export function shouldPollInvestigation(
  status: InvestigationStatus | undefined,
): boolean {
  return (
    status !== undefined &&
    [
      "planning",
      "collecting",
      "normalizing",
      "analyzing",
      "auditing",
      "reworking",
      "synthesizing",
      "cancelling",
    ].includes(status)
  );
}
