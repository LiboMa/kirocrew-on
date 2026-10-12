import { useMemo, useState } from "react";
import { Plus, Search } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { StatusBadge } from "@/components/StatusBadge";
import { useConsole } from "@/features/console/context";
import { formatTime } from "@/lib/labels";
import { elevated } from "@/lib/access";
import { RUN_FILTERS, filterRuns, statusText, type RunFilter } from "@/lib/session";
import { cn } from "@/lib/utils";

export function Sidebar() {
  const { runs, workflows, space, selected, select, open, users, me, allTeams, allSessions, setAllSessions } = useConsole();
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<RunFilter>("all");
  const scope = allSessions ? null : space;
  const visible = useMemo(() => filterRuns(runs, { space: scope, query, filter }).sort((a, b) => b.updated - a.updated),
    [runs, scope, query, filter]);
  const inSpace = useMemo(() => filterRuns(runs, { space: scope, query: "", filter: "all" }), [runs, scope]);
  const where = (run: (typeof runs)[number]) => (run.team_id ? "Team · " + (allTeams.find((t) => t.id === run.team_id)?.name ?? run.team_id)
    : "个人 · " + (users.find((u) => u.id === run.owner_id)?.username ?? run.owner_id));

  return (
    <Tabs defaultValue="sessions" className="flex min-h-0 flex-1 flex-col gap-0">
      <div className="px-4 pt-4 pb-3">
        <TabsList className="w-full">
          <TabsTrigger value="sessions">会话 <span className="text-muted-foreground">{inSpace.length}</span></TabsTrigger>
          <TabsTrigger value="workflows">工作流 <span className="text-muted-foreground">{workflows.length}</span></TabsTrigger>
        </TabsList>
      </div>
      <TabsContent value="sessions" className="flex min-h-0 flex-1 flex-col">
        <div className="space-y-3 px-4 pb-3">
          <div className="relative">
            <Search className="pointer-events-none absolute top-2.5 left-2.5 size-4 text-muted-foreground" aria-hidden />
            <Input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="搜索名称、需求、工作流或 ID"
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
              <button key={value} type="button" onClick={() => setFilter(value)} aria-pressed={filter === value}
                className={cn("rounded-full border px-2.5 py-1 text-xs transition-colors",
                  filter === value ? "border-primary bg-primary text-primary-foreground" : "bg-card text-muted-foreground hover:bg-accent")}>
                {label}
              </button>
            ))}
          </div>
        </div>
        <ScrollArea className="min-h-0 flex-1">
          <nav id="session-list" aria-label="会话列表" className="space-y-1 px-2 pb-4">
            {visible.map((run) => (
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
            {!visible.length && (
              <p className="px-3 py-6 text-center text-xs leading-6 text-muted-foreground">
                {inSpace.length ? "没有符合条件的会话。" : "此空间还没有开发任务。"}
              </p>
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
