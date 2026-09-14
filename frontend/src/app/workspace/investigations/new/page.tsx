"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { createInvestigation } from "@/core/investigations";

export default function NewInvestigationPage() {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [brief, setBrief] = useState("");
  const [competitors, setCompetitors] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    const names = competitors
      .split(/[,，\n]/)
      .map((value) => value.trim())
      .filter(Boolean);
    if (names.length < 2 || names.length > 5) {
      setError("请输入 2–5 个竞品");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const created = await createInvestigation({
        title,
        brief,
        scope: {
          market: "中国+全球",
          audience: "产品与战略团队",
          language: "zh-CN",
          time_range: "最近12个月",
          competitors: names,
          dimensions: ["功能", "定价", "定位", "用户", "壁垒"],
        },
      });
      window.location.assign(`/workspace/investigations/${created.id}`);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建失败");
      setSubmitting(false);
    }
  };

  return (
    <main className="mx-auto w-full max-w-3xl p-6 md:p-10">
      <Button variant="ghost" onClick={() => router.back()}>
        返回
      </Button>
      <form onSubmit={submit} className="mt-6 space-y-6 rounded-xl border p-6">
        <div>
          <h1 className="text-2xl font-semibold">新建竞品深度调研</h1>
          <p className="text-muted-foreground mt-2 text-sm">
            提交后先确认开题范围，再启动自动采集与审计。
          </p>
        </div>
        <label className="block space-y-2">
          <span className="text-sm font-medium">调研标题</span>
          <Input
            value={title}
            onChange={(event) => setTitle(event.target.value)}
            required
            maxLength={200}
          />
        </label>
        <label className="block space-y-2">
          <span className="text-sm font-medium">决策背景与目标</span>
          <Textarea
            value={brief}
            onChange={(event) => setBrief(event.target.value)}
            required
            minLength={10}
            rows={6}
          />
        </label>
        <label className="block space-y-2">
          <span className="text-sm font-medium">
            竞品（逗号或换行分隔，2–5 个）
          </span>
          <Textarea
            value={competitors}
            onChange={(event) => setCompetitors(event.target.value)}
            required
            rows={4}
          />
        </label>
        {error && <p className="text-destructive text-sm">{error}</p>}
        <Button type="submit" disabled={submitting}>
          {submitting ? "创建中…" : "生成开题范围"}
        </Button>
      </form>
    </main>
  );
}
