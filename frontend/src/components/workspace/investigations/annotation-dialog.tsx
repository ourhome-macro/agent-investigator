"use client";

import { useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import {
  submitAnnotation,
  type AnnotationInput,
  type Claim,
  type CoverageCell,
  type Investigation,
  type Report,
} from "@/core/investigations";
import { readableError } from "@/core/investigations/quality";

export interface FeedbackTarget {
  sectionKey: string;
  quote: string;
  claims: Claim[];
  cell?: CoverageCell;
}

export function AnnotationDialog({
  investigation,
  report,
  target,
  onClose,
  onSubmitted,
}: {
  investigation: Investigation;
  report: Report;
  target: FeedbackTarget;
  onClose: () => void;
  onSubmitted: () => Promise<void>;
}) {
  const [claimId, setClaimId] = useState(
    target.claims.length === 1 ? target.claims[0]!.id : "",
  );
  const [comment, setComment] = useState("");
  const [action, setAction] = useState<AnnotationInput["action"]>("recollect");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const attempt = useRef({ payload: "", key: "" });
  const policy = investigation.resource_policy ?? investigation.policy_snapshot;
  const submit = async () => {
    if (pending || comment.trim().length < 5 || (!target.cell && !claimId))
      return;
    const payload = {
      report_version: report.version,
      section_key: target.sectionKey,
      selected_text: target.quote,
      comment: comment.trim(),
      action: target.cell ? ("recollect" as const) : action,
      ...(target.cell
        ? {
            competitor_id: target.cell.competitor_id,
            dimension: target.cell.dimension,
          }
        : {
            claim_id: claimId,
            claim_version: report.structured_data?.claim_versions?.[claimId],
          }),
    };
    const signature = JSON.stringify(payload);
    if (attempt.current.payload !== signature)
      attempt.current = { payload: signature, key: crypto.randomUUID() };
    setPending(true);
    setError(null);
    try {
      await submitAnnotation(investigation.id, {
        ...payload,
        idempotency_key: attempt.current.key,
      });
      await onSubmitted();
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "提交失败");
    } finally {
      setPending(false);
    }
  };
  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !pending) onClose();
      }}
    >
      <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>针对这处内容补充研究</DialogTitle>
          <DialogDescription>
            绑定第 {report.version}{" "}
            版报告。系统会核对指定问题，生成新版本供你审核，原报告会保留。
          </DialogDescription>
        </DialogHeader>
        {target.cell ? (
          <p className="rounded-md border p-3 text-sm">
            {target.cell.competitor} · {target.cell.dimension}
            ：补充目前缺失的依据。
          </p>
        ) : (
          <label className="space-y-1 text-sm">
            <span>针对哪条结论或依据？</span>
            <select
              className="w-full rounded-md border p-2"
              value={claimId}
              onChange={(event) => setClaimId(event.target.value)}
            >
              <option value="">请选择需要核对的结论</option>
              {target.claims.map((claim) => (
                <option key={claim.id} value={claim.id}>
                  {claim.display_text ?? claim.text}
                </option>
              ))}
            </select>
          </label>
        )}
        {target.quote && (
          <blockquote className="max-h-36 overflow-auto border-l-2 pl-3 text-sm">
            {target.quote}
          </blockquote>
        )}
        {!target.cell && (
          <label className="space-y-1 text-sm">
            <span>希望怎样处理？</span>
            <select
              className="w-full rounded-md border p-2"
              value={action}
              onChange={(event) =>
                setAction(event.target.value as AnnotationInput["action"])
              }
            >
              <option value="recollect">补充资料并重新核对</option>
              <option value="revise">缩小或修正结论，再次核对</option>
              <option value="investigate_conflict">调查相互矛盾的说法</option>
            </select>
          </label>
        )}
        <label className="space-y-1 text-sm">
          <span>你的疑问或补充要求</span>
          <Textarea
            value={comment}
            onChange={(event) => setComment(event.target.value)}
            minLength={5}
            maxLength={3000}
            rows={4}
            placeholder="例如：这是否只适用于付费版？请查找官方说明并核对版本。"
          />
        </label>
        <p className="text-muted-foreground text-xs">
          本次补研最多使用{" "}
          {((policy?.refinement_tokens ?? 0) / 10000).toLocaleString()} 万 Token
          分析额度，执行时间上限 {policy?.refinement_minutes ?? 15}{" "}
          分钟。额度不是费用金额；每次研究最多可发起{" "}
          {policy?.max_annotations ?? 3} 次补研。
        </p>
        {error && (
          <p role="alert" className="text-destructive text-sm">
            {readableError(error)}
          </p>
        )}
        <Button
          onClick={() => void submit()}
          disabled={
            pending ||
            comment.trim().length < 5 ||
            (!target.cell && !claimId) ||
            !policy
          }
        >
          {pending ? "正在提交…" : "确认并开始补研"}
        </Button>
      </DialogContent>
    </Dialog>
  );
}
