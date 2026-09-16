"use client";

import { useState } from "react";

import { Button } from "@/components/ui/button";
import { MarkdownContent } from "@/components/workspace/messages/markdown-content";
import { type Claim, type Report } from "@/core/investigations";

import { type FeedbackTarget } from "./annotation-dialog";

export function ReportReview({
  report,
  claims,
  canRefine,
  onRefine,
}: {
  report: Report;
  claims: Claim[];
  canRefine: boolean;
  onRefine: (target: FeedbackTarget) => void;
}) {
  const [selection, setSelection] = useState<{
    sectionKey: string;
    text: string;
  } | null>(null);
  const sections = report.structured_data?.sections;
  if (!sections?.length)
    return (
      <MarkdownContent
        content={report.rendered_markdown}
        isLoading={false}
        className="mt-5"
      />
    );
  const capture = (sectionKey: string, element: HTMLElement) => {
    const current = window.getSelection();
    if (
      !current?.anchorNode ||
      !current.focusNode ||
      !element.contains(current.anchorNode) ||
      !element.contains(current.focusNode)
    )
      return;
    const text = current.toString().trim();
    setSelection(
      text.length > 0 && text.length <= 4000 ? { sectionKey, text } : null,
    );
  };
  return (
    <div className="mt-5 space-y-6">
      <p className="text-muted-foreground text-sm">
        选中一段正文后，可以针对其中的结论提出疑问；也可以直接使用本节下方的补研按钮。
      </p>
      {sections.map((section) => {
        const related = claims.filter(
          (claim) =>
            section.claim_ids.includes(claim.id) &&
            report.structured_data?.claim_versions?.[claim.id] !== undefined &&
            report.structured_data.claim_versions[claim.id] === claim.version &&
            !["rejected", "superseded"].includes(claim.status),
        );
        return (
          <article
            className="rounded-lg border p-4"
            key={section.id}
            id={`report-section-${section.type}`}
          >
            <h3 className="mb-3 text-lg font-medium">{section.title}</h3>
            <div
              onMouseUp={(event) => capture(section.type, event.currentTarget)}
              onKeyUp={(event) => capture(section.type, event.currentTarget)}
            >
              <MarkdownContent content={section.markdown} isLoading={false} />
            </div>
            {canRefine && related.length > 0 && (
              <Button
                className="mt-3"
                variant="outline"
                onClick={() =>
                  onRefine({
                    sectionKey: section.type,
                    quote:
                      selection?.sectionKey === section.type
                        ? selection.text
                        : "",
                    claims: related,
                  })
                }
              >
                {selection?.sectionKey === section.type
                  ? "对选中文字补研"
                  : "对本节结论提出疑问"}
              </Button>
            )}
          </article>
        );
      })}
    </div>
  );
}
