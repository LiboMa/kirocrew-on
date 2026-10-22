import { useEffect, useState } from "react";
import { Plus, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
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

export function Sidebar() {
  const { runs, workflows, selected, select, open, users, me, allTeams, allSessions, setAllSessions, filters, setFilters,
    spaceTotal, hasMore, loadMore, loadingMore, listError } = useConsole();
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
          {elevated(me) && (
            <button type="button" onClick={() => setAllSessions(!allSessions)} aria-pressed={allSessions}
              className={cn("w-full rounded-md border px-2.5 py-1.5 text-left text-xs transition-colors",
                allSessions ? "border-primary bg-primary/10 text-primary" : "bg-card text-muted-foreground hover:bg-accent")}>
              全部会话
            </button>
          )}
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
            {runs.map((run) => (
              <button key={run.id} type="button" data-run={run.id} onClick={() => select(run.id)}
                aria-current={run.id === selected ? "true" : undefined}
                className={cn("w-full rounded-lg border border-transparent px-3 py-2.5 text-left transition-colors hover:bg-card",
                  run.id === selected && "border-primary/25 bg-card shadow-xs")}>
                <strong className="line-clamp-2 text-[13px] leading-5 font-medium">{run.name || run.input}</strong>
                <span className="mt-1.5 flex items-center gap-2 text-[11px] text-muted-foreground">
                  <StatusBadge status={run.lifecycle === "closed" ? "stopped" : run.status} label={statusText(run)} />
                  <span>{run.progress.accepted}/{run.progress.total} 交接</span>
                  <span className="ml-auto font-mono">{formatTime(run.updated, true)}</span>
                </span>
                {allSessions && <span className="mt-1 block text-[11px] text-muted-foreground">{where(run)}</span>}
              </button>
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
