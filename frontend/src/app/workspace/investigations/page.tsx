"use client";

import { FlaskConical, Plus } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { listInvestigations, type Investigation } from "@/core/investigations";
import {
  readableError,
  researchStageLabel,
} from "@/core/investigations/quality";

export default function InvestigationsPage() {
  const [items, setItems] = useState<Investigation[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void listInvestigations()
      .then(setItems)
      .catch((reason: unknown) =>
        setError(reason instanceof Error ? reason.message : "加载失败"),
      );
  }, []);

  return (
    <main className="mx-auto w-full max-w-6xl space-y-8 p-6 md:p-10">
      <header className="flex items-center justify-between gap-4">
        <div>
          <p className="text-muted-foreground text-sm">研究工作台</p>
          <h1 className="text-3xl font-semibold">竞品深度调研</h1>
          <p className="text-muted-foreground mt-2">
            比较产品、核对原始资料，分清已知事实、来源说法和信息缺口。开始研究和发布报告前，都由你确认。
          </p>
        </div>
        <Button asChild>
          <Link href="/workspace/investigations/new">
            <Plus className="size-4" />
            新建调研
          </Link>
        </Button>
      </header>
      {error && (
        <div className="border-destructive/30 bg-destructive/10 text-destructive rounded-lg border p-4">
          {readableError(error)}
        </div>
      )}
      <section className="grid gap-4 md:grid-cols-2">
        {items.map((item) => (
          <Link
            key={item.id}
            href={`/workspace/investigations/${item.id}`}
            className="hover:border-primary rounded-xl border p-5 transition-colors"
          >
            <div className="flex items-start justify-between gap-3">
              <FlaskConical className="text-primary size-5" />
              <span className="bg-muted rounded-full px-2 py-1 text-xs">
                {researchStageLabel(item.status)}
              </span>
            </div>
            <h2 className="mt-4 text-lg font-medium">{item.title}</h2>
            <p className="text-muted-foreground mt-2 line-clamp-2 text-sm">
              {item.brief}
            </p>
            <p className="text-muted-foreground mt-4 text-xs">
              {item.scope.competitors.join(" · ")}
            </p>
          </Link>
        ))}
        {!error && items.length === 0 && (
          <div className="text-muted-foreground rounded-xl border border-dashed p-10 text-center md:col-span-2">
            还没有调研任务。
          </div>
        )}
      </section>
    </main>
  );
}
