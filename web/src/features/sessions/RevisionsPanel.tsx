import { useEffect, useState } from "react";
import { useRevisionDiff, useRevisions } from "@/api/hooks";
import type { DiffLine, Run, TaskDiff } from "@/api/types";
import { NativeSelect } from "@/components/NativeSelect";
import { ROLE_LABELS, formatTime } from "@/lib/labels";
import { cn } from "@/lib/utils";

const KINDS: Record<string, string> = { created: "创建", planned: "规划", continued: "续作", recovered: "自动纠错" };
const STATUS: Record<TaskDiff["status"], string> = { added: "新增", removed: "删除", changed: "修改", same: "未变化" };
const FIELD: Record<string, string> = {
  name: "名称", tool: "工具", model: "模型", effort: "Effort", kind: "类别", role: "角色", deliverables: "交付物", acceptance: "验收项",
  verify: "验证命令", depends_on: "依赖", paths: "可改动路径", skills: "方法",
};
const shown = (value: unknown, key: string) =>
  (Array.isArray(value) ? value.join("；") : key === "role" ? ROLE_LABELS[value as string] ?? value as string : value as string) || "默认";

const CONTEXT = 2;

/** Changed lines with two lines of context; longer unchanged stretches fold into one marker. */
function Lines({ lines }: { lines: DiffLine[] }) {
  const near = lines.map((_, i) => lines.slice(Math.max(0, i - CONTEXT), i + CONTEXT + 1).some((l) => l.op !== " "));
  const rows: ({ line: DiffLine; index: number } | { folded: number; index: number })[] = [];
  lines.forEach((line, index) => {
    if (near[index]) rows.push({ line, index });
    else if (rows.length && "folded" in rows[rows.length - 1]) (rows[rows.length - 1] as { folded: number }).folded += 1;
    else rows.push({ folded: 1, index });
  });
  return (
    <pre className="overflow-x-auto rounded-md bg-muted/40 py-1 text-xs leading-5">
      {rows.map((row) => "folded" in row ? (
        <div key={row.index} className="px-3 text-muted-foreground">… {row.folded} 行未变化 …</div>
      ) : (
        <div key={row.index} data-op={row.line.op} className={cn("px-3 whitespace-pre-wrap",
          row.line.op === "+" && "bg-emerald-500/10 text-emerald-700",
          row.line.op === "-" && "bg-destructive/10 text-destructive line-through")}>
          <span className="mr-2 select-none text-muted-foreground">{row.line.op === " " ? " " : row.line.op}</span>{row.line.text}
        </div>
      ))}
    </pre>
  );
}

/** Every change to the Session's requirement and tasks, and what changed between any two revisions (D32). */
export function RevisionsPanel({ run }: { run: Run }) {
  const { data: revisions = [], error } = useRevisions(run.id, run.version);
  const versions = revisions.map((r) => r.version);
  const latest = versions.at(-1) ?? null;
  const [to, setTo] = useState<number | null>(null);
  const [from, setFrom] = useState<number | null>(null);
  const target = to ?? latest;
  const before = from ?? (target !== null ? versions.filter((v) => v < target).at(-1) ?? null : null);
  useEffect(() => { setTo(null); setFrom(null); }, [run.id]);
  const diff = useRevisionDiff(run.id, before, target).data;
  const unchanged = diff?.tasks.filter((t) => t.status === "same").length ?? 0;

  return (
    <section aria-label="修订历史" id="revisions" className="space-y-3 rounded-xl border bg-card p-4">
      <h2 className="text-sm font-semibold">修订历史</h2>
      {error && <p className="text-sm text-muted-foreground">{(error as Error).message}</p>}
      <ol className="space-y-1">
        {[...revisions].reverse().map((revision) => (
          <li key={revision.version}>
            <button type="button" aria-pressed={revision.version === target}
              onClick={() => { setTo(revision.version); setFrom(null); }}
              className={cn("w-full rounded-md px-2.5 py-1.5 text-left text-xs hover:bg-accent",
                revision.version === target && "bg-primary/10")}>
              <span className="font-mono">v{revision.version}</span> ·{" "}
              {!revision.summary.startsWith(KINDS[revision.kind] ?? revision.kind) && <>{KINDS[revision.kind] ?? revision.kind} · </>}
              <span className="text-foreground">{revision.summary}</span>
              <span className="ml-2 text-muted-foreground">{formatTime(revision.created, true)} · {revision.actor.username ?? revision.actor.id}</span>
            </button>
          </li>
        ))}
      </ol>
      {revisions.length === 1 && (
        <p className="text-xs text-muted-foreground">尚无后续修订：续作、KiroCrew 规划与自动纠错都会在这里留下可对比的版本。</p>
      )}
      {revisions.length > 1 && target !== null && (
        <div className="space-y-3 border-t pt-3">
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <label htmlFor="revision-from">对比起点</label>
            <NativeSelect id="revision-from" value={String(before ?? "")} onChange={(e) => setFrom(Number(e.target.value))}>
              {versions.filter((v) => v < target).map((v) => <option key={v} value={v}>v{v}</option>)}
            </NativeSelect>
            <span>→</span>
            <label htmlFor="revision-to" className="sr-only">对比终点</label>
            <NativeSelect id="revision-to" value={String(target)} onChange={(e) => { setTo(Number(e.target.value)); setFrom(null); }}>
              {versions.filter((v) => v > versions[0]).map((v) => <option key={v} value={v}>v{v}</option>)}
            </NativeSelect>
          </div>
          {diff?.reconstructed && <p className="text-xs text-muted-foreground">部分修订早于快照记录，按当时记录的变化还原；规划追加的任务可能缺失。</p>}
          {diff && (
            <>
              <div className="space-y-1">
                <h3 className="text-xs font-semibold">需求</h3>
                {diff.input.every((l) => l.op === " ") ? <p className="text-xs text-muted-foreground">需求未变化</p> : <Lines lines={diff.input} />}
              </div>
              {diff.tasks.filter((t) => t.status !== "same").map((task) => (
                <div key={task.id} className="space-y-1">
                  <h3 className="flex flex-wrap items-center gap-2 text-xs font-semibold">
                    {task.name}<span className="rounded bg-muted px-1.5 font-normal">{STATUS[task.status]}</span>
                    {Object.entries(task.fields).map(([key, [a, b]]) => (
                      <span key={key} className="font-normal text-muted-foreground">{FIELD[key] ?? key} {shown(a, key)} → {shown(b, key)}</span>
                    ))}
                  </h3>
                  {task.prompt.some((l) => l.op !== " ") ? <Lines lines={task.prompt} /> : <p className="text-xs text-muted-foreground">提示词未变化</p>}
                </div>
              ))}
              {unchanged > 0 && <p className="text-xs text-muted-foreground">另有 {unchanged} 个任务未变化</p>}
            </>
          )}
        </div>
      )}
    </section>
  );
}
