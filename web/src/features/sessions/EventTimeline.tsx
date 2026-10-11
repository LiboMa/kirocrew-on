import { useMemo, useState } from "react";
import type { StreamState } from "@/api/events";
import type { RunEvent } from "@/api/types";
import { Input } from "@/components/ui/input";
import { EVENT_LABELS, STATES, formatTime } from "@/lib/labels";
import { cn } from "@/lib/utils";

const STREAM: Record<StreamState, [string, string]> = {
  connecting: ["正在连接实时状态…", "bg-muted-foreground"],
  connected: ["实时状态已连接", "bg-success"],
  reconnecting: ["状态流重连中…", "bg-warning"],
  revoked: ["访问授权已撤销", "bg-destructive"],
};

function detail(event: RunEvent): string {
  const payload = event.payload ?? {};
  return String(payload.task_id ?? (payload.status && STATES[payload.status]) ?? payload.error ?? "");
}

export function EventTimeline({ events, stream }: { events: RunEvent[]; stream: StreamState }) {
  const [query, setQuery] = useState("");
  const shown = useMemo(() => {
    const term = query.trim().toLowerCase();
    return [...events].reverse().filter((event) => !term ||
      [event.kind, EVENT_LABELS[event.kind], detail(event)].some((text) => text?.toLowerCase().includes(term))).slice(0, 200);
  }, [events, query]);
  const [label, dot] = STREAM[stream];
  return (
    <section aria-label="执行轨迹" className="rounded-xl border bg-card">
      <header className="flex items-center justify-between gap-3 border-b px-4 py-3">
        <h2 className="text-sm font-semibold">执行轨迹 <span className="font-normal text-muted-foreground">{events.length} 条</span></h2>
        <span id="stream-status" className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <span className={cn("size-1.5 rounded-full", dot)} aria-hidden />{label}
        </span>
      </header>
      <div className="px-4 pt-3">
        <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="筛选事件" aria-label="筛选事件"
          className="h-8 text-xs" type="search" />
      </div>
      <ol id="event-list" className="max-h-[32rem] space-y-0 overflow-y-auto px-4 py-3">
        {shown.map((event) => (
          <li key={event.id} className="relative border-l py-2 pl-4 text-xs">
            <span className="absolute top-3 -left-[3.5px] size-1.5 rounded-full bg-primary/60" aria-hidden />
            <time className="font-mono text-[11px] text-muted-foreground">{formatTime(event.created)}</time>
            <strong className="ml-2 font-medium">{EVENT_LABELS[event.kind] ?? event.kind}</strong>
            {detail(event) && <p className="mt-0.5 break-words text-muted-foreground">{detail(event)}</p>}
          </li>
        ))}
        {!shown.length && <li className="py-6 text-center text-xs text-muted-foreground">{events.length ? "没有匹配的事件。" : "等待第一条事件。"}</li>}
      </ol>
    </section>
  );
}
