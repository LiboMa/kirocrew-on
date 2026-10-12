import { useEffect, useState } from "react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { useObservation } from "@/api/hooks";
import type { Observation, ObservedMessage, Run } from "@/api/types";
import { Button } from "@/components/ui/button";

// Roles as KiroCrew names them (observed on EC2 2026-10-12).
const ROLE: Record<string, string> = { user: "用户", assistant: "KiroCrew 协调者", subagent: "子 Agent", inject: "平台消息",
  system: "系统" };
const ACTIVE = new Set(["preparing", "ready", "running", "diagnosing", "dispatch_unknown", "waiting_approval"]);

/** The run's native KiroCrew conversation, read through its Worker (D30). It needs no native login, so it also works
 * where 「在 KiroCrew 中打开」 does not (someone else's shared environment). */
export function NativeSessionPanel({ run }: { run: Run }) {
  const live = run.lifecycle === "open" && ACTIVE.has(run.status);
  // Once older pages are loaded, the refreshed newest page starts where they end, so no message falls between them.
  const [older, setOlder] = useState<{ messages: ObservedMessage[]; anchor: number; before: number | null; more: boolean } | null>(null);
  const [limit, setLimit] = useState(50);
  const [loading, setLoading] = useState(false);
  const query = useObservation(run.native_parent ? run.id : null, live, limit);
  const latest = query.data;
  const before = older ? older.before : latest?.next_before ?? null;
  const more = older ? older.more : !!latest?.has_more;
  const gap = older && latest?.next_before != null ? Math.max(0, latest.next_before - older.anchor) : 0;
  const total = latest?.total;
  useEffect(() => {
    if (older && total != null) setLimit(Math.min(100, Math.max(50, total - older.anchor)));
  }, [older, total]);

  const loadOlder = async () => {
    if (before == null) return;
    setLoading(true);
    try {
      const page = await api<Observation>(`/runs/${encodeURIComponent(run.id)}/observe?limit=50&before=${before}`);
      setOlder((current) => ({ messages: [...page.messages, ...(current?.messages ?? [])],
        anchor: current?.anchor ?? before, before: page.next_before, more: page.has_more }));
    } catch (error) {
      toast.error((error as Error).message);
    } finally {
      setLoading(false);
    }
  };

  return (
    <section aria-label="KiroCrew 会话" className="space-y-3 rounded-xl border bg-card p-4" id="native-session">
      <div className="flex flex-wrap items-center gap-2">
        <h2 className="text-sm font-semibold">KiroCrew 会话</h2>
        {latest && (
          <span className="text-xs text-muted-foreground">
            {latest.running ? "运行中" : "空闲"}
            {latest.total != null && ` · 共 ${latest.total} 条`}
            {latest.context_pct != null && ` · 上下文 ${latest.context_pct.toFixed(1)}%`}
          </span>
        )}
      </div>
      {!run.native_parent ? (
        <p className="text-sm text-muted-foreground">原生会话尚未建立，Worker 准备完成后在此显示协调者与子 Agent 的对话。</p>
      ) : query.error && !latest ? (
        <p className="text-sm text-destructive">{(query.error as Error).message}</p>
      ) : !latest ? (
        <p className="text-sm text-muted-foreground">正在读取原生会话…</p>
      ) : (
        <div className="max-h-[32rem] space-y-2 overflow-y-auto pr-1">
          {more && (
            <Button type="button" size="sm" variant="ghost" className="w-full text-xs" disabled={loading} onClick={loadOlder}>
              {loading ? "正在读取…" : "加载更早的消息"}
            </Button>
          )}
          {[...(older?.messages ?? []), ...latest.messages].map((message, index) => (
            <article key={index} className="rounded-lg bg-muted/40 px-3 py-2 text-sm">
              {older && gap > 0 && index === older.messages.length && (
                <p className="mb-2 text-center text-xs text-muted-foreground">… 中间 {gap} 条较新的消息未显示 …</p>
              )}
              <header className="mb-1 flex items-center gap-2 text-[11px] text-muted-foreground">
                <span className="font-medium text-foreground/80">{ROLE[message.role] ?? message.role}</span>
                {message.ts && <time dateTime={message.ts}>{new Date(message.ts).toLocaleString()}</time>}
                {message.truncated && <span>· 已截断，完整内容在 KiroCrew 中查看</span>}
              </header>
              <p className="leading-6 break-words whitespace-pre-wrap">{message.content}</p>
            </article>
          ))}
          {!latest.messages.length && !older?.messages.length && <p className="text-sm text-muted-foreground">原生会话中还没有消息。</p>}
        </div>
      )}
    </section>
  );
}
