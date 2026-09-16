"use client";

import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  getEvidenceSnapshot,
  type Claim,
  type EvidenceSnapshot,
} from "@/core/investigations";
import { readableError } from "@/core/investigations/quality";

export type SourceSelection = {
  evidenceId: string;
  binding?: Claim["evidence_bindings"][number];
};

export function SourceDialog({
  investigationId,
  selection,
  onClose,
}: {
  investigationId: string;
  selection: SourceSelection | null;
  onClose: () => void;
}) {
  const [snapshot, setSnapshot] = useState<EvidenceSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [full, setFull] = useState(false);
  useEffect(() => {
    setSnapshot(null);
    setError(null);
    setFull(false);
    if (!selection) return;
    const controller = new AbortController();
    void getEvidenceSnapshot(
      investigationId,
      selection.evidenceId,
      controller.signal,
    )
      .then((value) => {
        if (controller.signal.aborted) return;
        if (
          selection.binding?.snapshot_sha256 &&
          selection.binding.snapshot_sha256 !== value.sha256
        )
          throw new Error("保存的原文版本与这条引用不一致，请刷新结论后重试。");
        setSnapshot(value);
      })
      .catch((reason: Error) => {
        if (!controller.signal.aborted) setError(reason.message);
      });
    return () => controller.abort();
  }, [investigationId, selection]);
  const quote = selection?.binding?.verbatim_quote;
  const recordedStart = selection?.binding?.quote_start;
  const recordedEnd = selection?.binding?.quote_end;
  const offset =
    quote && snapshot
      ? recordedStart !== undefined &&
        recordedEnd !== undefined &&
        snapshot.content.slice(recordedStart, recordedEnd) === quote
        ? recordedStart
        : snapshot.content.indexOf(quote)
      : -1;
  const start = !full && offset >= 0 ? Math.max(0, offset - 800) : 0;
  const end =
    !full && offset >= 0
      ? Math.min(
          snapshot?.content.length ?? 0,
          offset + (quote?.length ?? 0) + 800,
        )
      : (snapshot?.content.length ?? 0);
  return (
    <Dialog
      open={selection !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
    >
      <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>{snapshot?.title ?? "原文与引用"}</DialogTitle>
          <DialogDescription>
            这是收集时保存的正文。高亮部分对应结论引用，原网站之后的修改不会覆盖这份记录。
          </DialogDescription>
        </DialogHeader>
        {error ? (
          <p role="alert">{readableError(error)}</p>
        ) : snapshot ? (
          <>
            <a
              className="text-primary text-sm"
              href={snapshot.source_url}
              target="_blank"
              rel="noreferrer"
            >
              打开原网站 ↗
            </a>
            <p className="text-muted-foreground text-xs">
              收集时间：
              {new Date(snapshot.retrieved_at).toLocaleString("zh-CN")}
            </p>
            {offset >= 0 && (
              <Button
                variant="outline"
                onClick={() => setFull((value) => !value)}
              >
                {full ? "只看引用附近" : "显示完整正文"}
              </Button>
            )}
            <pre className="bg-muted rounded-lg p-4 text-sm break-words whitespace-pre-wrap">
              {offset >= 0 ? (
                <>
                  {start > 0 ? "…\n" : ""}
                  {snapshot.content.slice(start, offset)}
                  <mark>
                    {snapshot.content.slice(
                      offset,
                      offset + (quote?.length ?? 0),
                    )}
                  </mark>
                  {snapshot.content.slice(offset + (quote?.length ?? 0), end)}
                  {end < snapshot.content.length ? "\n…" : ""}
                </>
              ) : (
                snapshot.content
              )}
            </pre>
            <details className="text-muted-foreground text-xs">
              <summary>原文校验信息</summary>
              <p className="break-all">SHA-256：{snapshot.sha256}</p>
            </details>
          </>
        ) : (
          <p>正在加载已保存的原文…</p>
        )}
      </DialogContent>
    </Dialog>
  );
}
