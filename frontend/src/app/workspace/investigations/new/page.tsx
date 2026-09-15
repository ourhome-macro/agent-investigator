"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { createInvestigation } from "@/core/investigations";
import { readableError } from "@/core/investigations/quality";

const dimensions = ["功能", "定价", "定位", "用户", "壁垒"];

export default function NewInvestigationPage() {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [brief, setBrief] = useState("");
  const [competitors, setCompetitors] = useState("");
  const [requiredDimensions, setRequiredDimensions] = useState<string[]>([]);
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
          dimensions,
          required_dimensions: requiredDimensions,
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
            先告诉我们要比较哪些产品、准备做什么决策。下一步会让你确认研究范围，再开始查找资料和核对结论。
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
          <p className="text-muted-foreground text-xs">
            例如：我们想做一款播放器，需要比较离线播放、投屏和收费方式，判断哪些功能值得优先开发。
          </p>
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
        <fieldset className="space-y-3">
          <legend className="text-sm font-medium">
            必须回答的比较项目（可选）
          </legend>
          <p className="text-muted-foreground text-xs">
            勾选后，该项资料不足会标记研究未完成。不勾选时，每个竞品需有基本事实依据，其他信息缺口会在报告中注明。
          </p>
          <div className="flex flex-wrap gap-4">
            {dimensions.map((dimension) => (
              <label
                key={dimension}
                className="flex items-center gap-2 text-sm"
              >
                <input
                  type="checkbox"
                  checked={requiredDimensions.includes(dimension)}
                  onChange={(event) =>
                    setRequiredDimensions((current) =>
                      event.target.checked
                        ? [...current, dimension]
                        : current.filter((item) => item !== dimension),
                    )
                  }
                />
                {dimension}
              </label>
            ))}
          </div>
        </fieldset>
        {error && (
          <p className="text-destructive text-sm">{readableError(error)}</p>
        )}
        <Button type="submit" disabled={submitting}>
          {submitting ? "创建中…" : "下一步：确认研究范围"}
        </Button>
      </form>
    </main>
  );
}
