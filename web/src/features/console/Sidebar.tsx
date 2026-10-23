import { useEffect, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { MoreHorizontal, Pin, Plus, Search, Star } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { keys, usePinnedRuns } from "@/api/hooks";
import type { Run } from "@/api/types";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { StatusBadge } from "@/components/StatusBadge";
import { useConsole } from "@/features/console/context";
import { RunFilters } from "@/features/console/RunFilters";
import { formatTime } from "@/lib/labels";
import { elevated } from "@/lib/access";
import { activeFilters } from "@/lib/runList";
import { RUN_FILTERS, statusText } from "@/lib/session";
import { cn } from "@/lib/utils";

/** One Session in the list, with its own menu (owner 2026-10-22: 收藏、置顶、删除、分享). */
function SessionRow({ run, where }: { run: Run; where: string | null }) {
  const { selected, select, open } = useConsole();
  const client = useQueryClient();
  const marks = run.marks ?? { pinned: false, starred: false };
  const title = run.name || run.input;
  const mark = (patch: Partial<typeof marks>) =>
    api("/runs/" + encodeURIComponent(run.id) + "/marks", "POST", patch)
      .then(() => client.invalidateQueries({ queryKey: keys.runs }))
      .catch((error: Error) => toast.error(error.message));
  return (
    <div className="group relative">
      <button type="button" data-run={run.id} onClick={() => select(run.id)}
        aria-current={run.id === selected ? "true" : undefined}
        className={cn("w-full rounded-lg border border-transparent px-3 py-2.5 pr-9 text-left transition-colors hover:bg-card",
          run.id === selected && "border-primary/25 bg-card shadow-xs")}>
        <strong className="line-clamp-2 text-[13px] leading-5 font-medium">
          {marks.pinned && <Pin className="mr-1 inline size-3 text-primary" aria-label="已置顶" />}
          {marks.starred && <Star className="mr-1 inline size-3 fill-amber-400 text-amber-500" aria-label="已收藏" />}
          {title}
        </strong>
        <span className="mt-1.5 flex items-center gap-2 text-[11px] text-muted-foreground">
          <StatusBadge status={run.lifecycle === "closed" ? "stopped" : run.status} label={statusText(run)} />
          <span>{run.progress.accepted}/{run.progress.total} 交接</span>
          <span className="ml-auto font-mono">{formatTime(run.updated, true)}</span>
        </span>
        {where && <span className="mt-1 block text-[11px] text-muted-foreground">{where}</span>}
      </button>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button type="button" size="icon-sm" variant="ghost" aria-label={"会话操作：" + title}
            className="absolute top-1.5 right-1.5 text-muted-foreground opacity-70 group-hover:opacity-100 focus-visible:opacity-100">
            <MoreHorizontal />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="end" className="w-40">
          <DropdownMenuItem onSelect={() => void mark({ pinned: !marks.pinned })}>{marks.pinned ? "取消置顶" : "置顶"}</DropdownMenuItem>
          <DropdownMenuItem onSelect={() => void mark({ starred: !marks.starred })}>{marks.starred ? "取消收藏" : "收藏"}</DropdownMenuItem>
          {(run.permissions?.share || run.permissions?.delete) && <DropdownMenuSeparator />}
          {run.permissions?.share && <DropdownMenuItem onSelect={() => open({ kind: "run-share", run })}>共享…</DropdownMenuItem>}
          {run.permissions?.delete && (
            <DropdownMenuItem className="text-destructive" onSelect={() => open({ kind: "control", run, action: "delete" })}>删除…</DropdownMenuItem>
          )}
        </DropdownMenuContent>
      </DropdownMenu>
    </div>
  );
}

export function Sidebar() {
  const { runs, workflows, open, users, me, allTeams, allSessions, setAllSessions, sharedSessions,
    setSharedSessions, filters, setFilters, space,
    spaceTotal, hasMore, loadMore, loadingMore, listError } = useConsole();
  const found = usePinnedRuns(allSessions || sharedSessions ? null : space, allSessions).data;
  const pinned = Array.isArray(found) ? found : [];   // an older platform answers without marks
  const pinnedIds = new Set(pinned.map((run) => run.id));
  // D56: filtered on the server, newest first; the search is sent once typing pauses.
  const [query, setQuery] = useState(filters.q);
  useEffect(() => {
    if (query.trim() === filters.q) return;
    const timer = setTimeout(() => setFilters({ ...filters, q: query.trim() }), 300);
    return () => clearTimeout(timer);
  }, [query, filters, setFilters]);
  const filtered = !!filters.q || filters.group !== "all" || activeFilters(filters).length > 0;
  const where = (run: (typeof runs)[number]) => (run.team_id ? "Team · " + (allTeams.find((t) => t.id === run.team_id)?.name ?? run.team_id)
    : "个人 · " + (users.find((u) => u.id === run.owner_id)?.username ?? run.owner_id));
  const line = (run: Run) => (allSessions || sharedSessions ? where(run) + (sharedSessions && run.access?.via === "share"
    ? ` · ${run.access.role === "collaborator" ? "协作" : "查看"}${run.access.by ? ` · 由 ${run.access.by} 共享` : ""}` : "") : null);

  return (
    <Tabs defaultValue="sessions" className="flex min-h-0 flex-1 flex-col gap-0">
      <div className="px-4 pt-4 pb-3">
        <TabsList className="w-full">
          <TabsTrigger value="sessions">会话 <span className="text-muted-foreground">{spaceTotal}</span></TabsTrigger>
          <TabsTrigger value="workflows">工作流 <span className="text-muted-foreground">{workflows.length}</span></TabsTrigger>
        </TabsList>
      </div>
      <TabsContent value="sessions" className="flex min-h-0 flex-1 flex-col">
        <div className="space-y-3 px-4 pb-3">
          <div className="relative">
            <Search className="pointer-events-none absolute top-2.5 left-2.5 size-4 text-muted-foreground" aria-hidden />
            <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索需求、任务说明、结论或 ID"
              className="h-9 bg-card pl-8" aria-label="搜索会话" type="search" />
          </div>
          <div className="flex gap-1.5">
            <button type="button" onClick={() => setSharedSessions(!sharedSessions)} aria-pressed={sharedSessions}
              className={cn("flex-1 rounded-md border px-2.5 py-1.5 text-left text-xs transition-colors",
                sharedSessions ? "border-primary bg-primary/10 text-primary" : "bg-card text-muted-foreground hover:bg-accent")}>
              共享给我
            </button>
            {elevated(me) && (
              <button type="button" onClick={() => setAllSessions(!allSessions)} aria-pressed={allSessions}
                className={cn("flex-1 rounded-md border px-2.5 py-1.5 text-left text-xs transition-colors",
                  allSessions ? "border-primary bg-primary/10 text-primary" : "bg-card text-muted-foreground hover:bg-accent")}>
                全部会话
              </button>
            )}
          </div>
          <div className="flex flex-wrap gap-1.5" role="group" aria-label="按状态筛选">
            {RUN_FILTERS.map(([value, label]) => (
              <button key={value} type="button" onClick={() => setFilters({ ...filters, group: value })} aria-pressed={filters.group === value}
                className={cn("rounded-full border px-2.5 py-1 text-xs transition-colors",
                  filters.group === value ? "border-primary bg-primary text-primary-foreground" : "bg-card text-muted-foreground hover:bg-accent")}>
                {label}
              </button>
            ))}
          </div>
          <RunFilters />
        </div>
        <ScrollArea className="min-h-0 flex-1">
          <nav id="session-list" aria-label="会话列表" className="space-y-1 px-2 pb-4">
            {pinned.length > 0 && filters.group !== "starred" && (
              <section aria-label="置顶" className="space-y-1 border-b pb-2">
                <h3 className="px-3 pt-1 text-[11px] font-medium text-muted-foreground">置顶</h3>
                {pinned.map((run) => <SessionRow key={run.id} run={run} where={line(run)} />)}
              </section>
            )}
            {runs.filter((run) => filters.group === "starred" || !pinnedIds.has(run.id)).map((run) => (
              <SessionRow key={run.id} run={run} where={line(run)} />
            ))}
            {listError && <p role="alert" className="px-3 py-2 text-xs leading-5 text-destructive">会话列表读取失败：{listError}</p>}
            {!runs.length && !listError && (
              <p className="px-3 py-6 text-center text-xs leading-6 text-muted-foreground">
                {spaceTotal || filtered ? "没有符合条件的会话。" : "此空间还没有开发任务。"}
              </p>
            )}
            {hasMore && (
              <Button type="button" variant="ghost" size="sm" className="w-full text-xs" onClick={loadMore} disabled={loadingMore}>
                {loadingMore ? "加载中…" : "加载更多"}
              </Button>
            )}
          </nav>
        </ScrollArea>
      </TabsContent>
      <TabsContent value="workflows" className="flex min-h-0 flex-1 flex-col">
        <div className="px-4 pb-3">
          <Button variant="outline" size="sm" className="w-full" id="new-flow"
            onClick={() => open({ kind: "workflow", flow: null })}><Plus /> 新建工作流</Button>
        </div>
        <ScrollArea className="min-h-0 flex-1">
          <nav id="flow-list" aria-label="工作流库" className="space-y-1 px-2 pb-4">
            {workflows.map((flow) => (
              <button key={flow.id} type="button" data-flow={flow.id} onClick={() => open({ kind: "workflow", flow })}
                className="w-full rounded-lg px-3 py-2.5 text-left transition-colors hover:bg-card">
                <strong className="block truncate text-[13px] font-medium">{flow.name}</strong>
                <small className="text-[11px] text-muted-foreground">
                  v{flow.version} · {flow.steps.length} Tasks · {flow.owner_id === me.id ? "我的" :
                    users.find((u) => u.id === flow.owner_id)?.username ?? "已共享"}{flow.archived ? " · 已归档" : ""}
                </small>
              </button>
            ))}
            {!workflows.length && <p className="px-3 py-6 text-center text-xs text-muted-foreground">从模板构建你的第一个工作流。</p>}
          </nav>
        </ScrollArea>
      </TabsContent>
    </Tabs>
  );
}
