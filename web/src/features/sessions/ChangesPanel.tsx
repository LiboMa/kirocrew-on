import { useEffect, useState } from "react";
import { useChanges } from "@/api/hooks";
import type { ChangeEntry, Run } from "@/api/types";
import { Button } from "@/components/ui/button";
import { NativeSelect } from "@/components/NativeSelect";
import { eventLabel, formatTime } from "@/lib/labels";

const CLIENTS: Record<string, string> = { console: "控制台", mcp: "Coding Agent", api: "API" };
const SYSTEM: Record<string, string> = { engine: "Engine", worker: "Worker", kirocrew: "KiroCrew", recovery: "协调者", legacy: "系统（旧记录）" };
const FIELDS: Record<string, string> = {
  status: "状态", input: "需求", desired: "期望", lifecycle: "生命周期", worker_id: "Worker", native_parent: "原生会话",
  workspace: "工作区", version: "版本", error: "错误", spec: "任务配置", current_attempt: "当前执行", native_id: "原生任务",
  result: "结果", evidence: "证据", snapshot: "会话配置", context_hash: "上下文", decision: "决定",
};

/** A recorded value: long text is kept only as its length (D34 §4.1). */
function value(v: unknown): string {
  if (v === null || v === undefined || v === "") return "空";
  if (typeof v === "object" && v !== null && "chars" in v) return `${(v as { chars: number }).chars} 字符`;
  if (typeof v === "boolean") return v ? "是" : "否";
  if (Array.isArray(v)) return v.map(value).join("；");
  return typeof v === "object" ? JSON.stringify(v) : String(v);
}

function fields(changes: Record<string, [unknown, unknown]>): string {
  return Object.entries(changes).map(([key, [a, b]]) => `${FIELDS[key] ?? key}：${value(a)} → ${value(b)}`).join("　");
}

function who(actor: ChangeEntry["actor"]): string {
  if (actor.kind === "user") {
    const user = actor as Extract<ChangeEntry["actor"], { kind: "user" }>;
    return (user.username ?? user.id) + (user.client ? " · " + CLIENTS[user.client] : "");
  }
  return SYSTEM[actor.kind] ?? actor.kind;
}

/** Every write to the Session, newest first: who changed what, from what, to what and why (D34). */
export function ChangesPanel({ run }: { run: Run }) {
  const [open, setOpen] = useState(false);
  const [source, setSource] = useState<"" | "user" | "system">("");
  useEffect(() => { setOpen(false); setSource(""); }, [run.id]);
  const { data: entries = [], error } = useChanges(run.id, source, open);
  return (
    <section aria-label="变更记录" id="changes" className="space-y-3 rounded-xl border bg-card p-4">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold">变更记录</h2>
        {open ? (
          <>
            <label htmlFor="changes-who" className="sr-only">变更来源</label>
            <NativeSelect id="changes-who" aria-label="变更来源" value={source}
              onChange={(e) => setSource(e.target.value as "" | "user" | "system")}>
              <option value="">全部</option><option value="user">人</option><option value="system">系统</option>
            </NativeSelect>
          </>
        ) : (
          <Button type="button" size="sm" variant="outline" onClick={() => setOpen(true)}>显示变更记录</Button>
        )}
      </div>
      {!open && <p className="text-xs text-muted-foreground">每一次写入都记录操作者、来源、字段前后值与原因。</p>}
      {open && error && <p className="text-sm text-muted-foreground">{(error as Error).message}</p>}
      {open && (
        <ol className="space-y-1.5">
          {entries.map((entry) => (
            <li key={entry.id} data-change={entry.id} className="rounded-md border px-2.5 py-1.5 text-xs">
              <div className="flex flex-wrap items-center gap-x-2">
                <span className="font-medium">{eventLabel({ kind: entry.kind, payload: entry.detail })}</span>
                <span className="text-muted-foreground">{who(entry.actor)}</span>
                <span className="ml-auto text-muted-foreground">{formatTime(entry.at, true)}</span>
              </div>
              {entry.reason && <p className="text-muted-foreground">{entry.reason}</p>}
              {entry.changes && !Array.isArray(entry.changes) && <p className="break-words">{fields(entry.changes)}</p>}
              {Array.isArray(entry.changes) && entry.changes.map((row) => (
                <p key={row.id} className="break-words">{row.id}：{fields(row.changes).replace(/：/, " ")}</p>
              ))}
            </li>
          ))}
          {!entries.length && !error && <li className="text-xs text-muted-foreground">暂无记录。</li>}
        </ol>
      )}
    </section>
  );
}
