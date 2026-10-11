import {
  CircleStop, Download, ExternalLink, ListRestart, MoreHorizontal, Power, RotateCcw, Stethoscope, Trash2, UserCog,
} from "lucide-react";
import { useRun, useRunEvents } from "@/api/hooks";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { StatusBadge } from "@/components/StatusBadge";
import { useConsole } from "@/features/console/context";
import { EventTimeline } from "@/features/sessions/EventTimeline";
import { RecoveryPanel } from "@/features/sessions/RecoveryPanel";
import { TaskList } from "@/features/sessions/TaskList";
import { useSessionActions } from "@/features/sessions/useSessionActions";
import { canContinue, canDelete, canRecover, canStop, closeAction, crewEntryReason, statusText } from "@/lib/session";

export function SessionView({ id }: { id: string }) {
  const { teams, workflows, open } = useConsole();
  const query = useRun(id);
  const { events, stream } = useRunEvents(id);
  const run = query.data;
  const actions = useSessionActions(run);

  if (!run) {
    return (
      <div className="space-y-4 p-6 sm:p-10">
        {query.error ? <p className="text-sm text-destructive">{query.error.message}</p> : (
          <><Skeleton className="h-8 w-2/3" /><Skeleton className="h-24 w-full" /><Skeleton className="h-64 w-full" /></>
        )}
      </div>
    );
  }

  const title = run.name || run.input;
  const flowName = run.workflow_name ?? workflows.find((f) => f.id === run.workflow_id)?.name ?? "定制任务";
  const crewReason = crewEntryReason(run);
  const close = closeAction(run);
  const design = run.purpose === "workflow_design";
  const percent = run.progress.total ? (100 * run.progress.accepted) / run.progress.total : 0;

  return (
    <div className="mx-auto max-w-6xl space-y-5 px-4 py-6 sm:px-8" id="run-view">
      <header className="space-y-4 rounded-xl border bg-card p-5 shadow-xs sm:p-6">
        <div className="flex flex-wrap items-start gap-4">
          <div className="min-w-0 flex-1 space-y-1.5">
            <p className="text-[11px] font-semibold tracking-[0.16em] text-muted-foreground uppercase">
              {teams.find((t) => t.id === run.team_id)?.name ?? "Personal"} / {flowName}
            </p>
            <h1 id="run-title" className="text-xl leading-snug font-bold break-words sm:text-2xl" title={run.input}>
              {title.length > 125 ? title.slice(0, 125) + "…" : title}
            </h1>
            {run.name && run.name !== run.input && (
              <p id="run-goal" className="line-clamp-3 text-sm leading-6 whitespace-pre-wrap text-muted-foreground">{run.input}</p>
            )}
            <p id="run-id" className="font-mono text-[11px] text-muted-foreground">{run.id} · revision {run.version}</p>
          </div>
          <div className="flex flex-col items-end gap-2">
            <StatusBadge status={run.lifecycle === "closed" ? "stopped" : run.status} label={statusText(run)} className="text-xs" />
            <div className="w-44 space-y-1 text-right">
              <span className="text-xs text-muted-foreground">交接进度 {run.progress.accepted} / {run.progress.total}</span>
              <Progress value={percent} aria-label="交接进度" />
            </div>
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2" role="toolbar" aria-label="会话操作">
          <Button size="sm" onClick={() => open({ kind: "continue" })} disabled={!canContinue(run)} id="run-continue">
            <ListRestart /> 续作
          </Button>
          {!design && (
            <Button size="sm" variant="outline" onClick={actions.recover} disabled={!canRecover(run) || !!actions.busy} id="run-recover">
              <Stethoscope /> 请求协调者诊断
            </Button>
          )}
          <Tooltip>
            <TooltipTrigger asChild>
              <span tabIndex={crewReason ? 0 : -1}>
                <Button size="sm" variant="outline" onClick={actions.openCrew} disabled={!!crewReason || actions.opening} id="run-open-crew">
                  <ExternalLink /> {actions.opening ? "正在连接 KiroCrew…" : "在 KiroCrew 中打开"}
                </Button>
              </span>
            </TooltipTrigger>
            <TooltipContent>{crewReason || "在新标签页打开此 Session 对应的 KiroCrew 原生父会话"}</TooltipContent>
          </Tooltip>
          <Button size="sm" variant="outline" onClick={actions.stop} disabled={!canStop(run) || !!actions.busy} id="run-stop">
            <CircleStop /> 停止
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button size="sm" variant="ghost" aria-label="更多操作"><MoreHorizontal /></Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-52">
              <DropdownMenuItem disabled={close.disabled} onSelect={() => open({ kind: "control", action: close.action })}>
                {close.action === "close" ? <Power /> : <RotateCcw />} {close.label}
              </DropdownMenuItem>
              {!design && (
                <DropdownMenuItem onSelect={() => open({ kind: "launch", seed: { steps: run.tasks.map((t) => structuredClone(t.spec)) } })}>
                  <ListRestart /> 按此任务列表发起新开发
                </DropdownMenuItem>
              )}
              <DropdownMenuItem onSelect={actions.exportRun}><Download /> 导出 JSON</DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" disabled={!canDelete(run)} onSelect={() => open({ kind: "control", action: "delete" })}>
                <Trash2 /> 删除会话
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
        {crewReason && <p id="crew-entry-hint" className="text-xs text-muted-foreground">{crewReason}</p>}
      </header>

      {run.attention && (
        <Alert variant="destructive" id="run-attention" className="border-destructive/30 bg-destructive/5">
          <AlertTitle id="attention-title">{run.attention.title}</AlertTitle>
          <AlertDescription className="space-y-1">
            {run.attention.detail && <p id="attention-detail">{run.attention.detail}</p>}
            <p id="attention-action" className="text-foreground/80">
              {[run.attention.task_name && `当前任务：${run.attention.task_name}`, run.attention.action].filter(Boolean).join(" · ")}
            </p>
          </AlertDescription>
        </Alert>
      )}
      {run.error && !run.attention && (
        <Alert variant="destructive" id="run-error"><AlertDescription>{run.error}</AlertDescription></Alert>
      )}
      {run.status === "waiting_worker" && (
        <Alert id="worker-notice" className="border-warning/30 bg-warning/5">
          <AlertDescription className="flex flex-wrap items-center gap-3">
            需求已保存。请选择此开发空间的 Worker 后开始执行。
            <Button size="sm" variant="outline" onClick={() => open({ kind: "assign" })} id="assign-worker"><UserCog /> 分配 Worker</Button>
          </AlertDescription>
        </Alert>
      )}
      {run.recovery && (
        <p id="recovery-policy" className="text-xs text-muted-foreground">
          {run.recovery.policy.enabled ? `自动纠错已开启 · 最多 ${run.recovery.policy.max_cycles} 轮` : "自动纠错未开启 · 可手动诊断"}
        </p>
      )}
      <RecoveryPanel run={run} />

      <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_22rem]">
        <section className="space-y-3">
          <h2 className="text-sm font-semibold">开发任务</h2>
          <TaskList run={run} onContinue={(taskId) => open({ kind: "continue", taskId })} />
        </section>
        <aside className="space-y-4">
          <EventTimeline events={events} stream={stream} />
          <section className="space-y-2 rounded-xl border bg-card p-4 text-xs" id="native-details">
            <h2 className="text-sm font-semibold">Workspace 与原生会话</h2>
            <dl className="space-y-2">
              <div><dt className="text-muted-foreground">Workspace</dt><dd className="font-mono break-all">{run.workspace || "尚未准备"}</dd></div>
              <div><dt className="text-muted-foreground">原生父对话</dt><dd className="font-mono break-all">{run.native_parent || "尚未建立"}</dd></div>
              <div><dt className="text-muted-foreground">来源</dt><dd className="font-mono break-all">{run.source.url ? `${run.source.url}${run.source.ref ? " @ " + run.source.ref : ""}` : "新建项目"}</dd></div>
            </dl>
            <p className="text-muted-foreground">运行与源码由此空间的 Worker 保管。</p>
          </section>
        </aside>
      </div>
    </div>
  );
}
