import {
  CircleStop, Copy, Download, Eye, ExternalLink, FolderDown, ListRestart, MonitorPlay, MoreHorizontal, Power, RotateCcw, Stethoscope,
  Trash2,
  UserCog,
} from "lucide-react";
import { toast } from "sonner";
import { useRun, useRunApp, useRunEvents } from "@/api/hooks";
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
import { NativeSessionPanel } from "@/features/sessions/NativeSessionPanel";
import { AppPreviewPanel } from "@/features/sessions/AppPreviewPanel";
import { ChangesPanel } from "@/features/sessions/ChangesPanel";
import { CredentialControl } from "@/features/sessions/CredentialControl";
import { PublishControl } from "@/features/sessions/PublishControl";
import { RevisionsPanel } from "@/features/sessions/RevisionsPanel";
import { RecoveryPanel } from "@/features/sessions/RecoveryPanel";
import { ReportButton } from "@/features/sessions/ReportDialog";
import { TaskList } from "@/features/sessions/TaskList";
import { useSessionActions } from "@/features/sessions/useSessionActions";
import { bindingLabel } from "@/lib/credentials";
import { canContinue, canDelete, canRecover, canStop, closeAction, crewEntryReason, deleteReason, resumeReason, statusText } from "@/lib/session";

export function SessionView({ id }: { id: string }) {
  const { allTeams, workflows, workers, me, open } = useConsole();
  const query = useRun(id);
  const { events, stream } = useRunEvents(id);
  const run = query.data;
  const actions = useSessionActions(run);
  const app = useRunApp(id, !!run?.worker_id).data;   // shared with the panel: one request

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
  const environment = workers.find((w) => w.id === run.worker_id);
  // A shared environment's native login covers its whole Gateway (K03): only its boundary owner may open it (D28).
  const crewReason = crewEntryReason(run) ||
    (environment?.scope === "shared" && environment.owner_id !== me.id
      ? "共享执行环境不开放原生 KiroCrew 入口；可在本页「KiroCrew 会话」查看。"
      : run.permissions?.native === false ? "原生 KiroCrew 入口不因管理员身份开放；可在本页「KiroCrew 会话」查看。" : "");
  const close = closeAction(run);
  const design = run.purpose === "workflow_design";
  const percent = run.progress.total ? (100 * run.progress.accepted) / run.progress.total : 0;

  return (
    <div className="mx-auto max-w-6xl space-y-5 px-4 py-6 sm:px-8" id="run-view">
      <header className="space-y-4 rounded-xl border bg-card p-5 shadow-xs sm:p-6">
        <div className="flex flex-wrap items-start gap-4">
          <div className="min-w-0 flex-1 space-y-1.5">
            <p className="text-[11px] font-semibold tracking-[0.16em] text-muted-foreground uppercase">
              {run.team_id ? allTeams.find((t) => t.id === run.team_id)?.name ?? "Team" : "Personal"} / {flowName}
            </p>
            <h1 id="run-title" className="text-xl leading-snug font-bold break-words sm:text-2xl" title={run.input}>
              {title.length > 125 ? title.slice(0, 125) + "…" : title}
            </h1>
            {run.name && run.name !== run.input && (
              <p id="run-goal" className="line-clamp-3 text-sm leading-6 whitespace-pre-wrap text-muted-foreground">{run.input}</p>
            )}
            <p id="run-id" className="font-mono text-[11px] text-muted-foreground">{run.id} · revision {run.version}</p>
            {run.created_by && (
              <p id="run-owner" className="text-xs text-muted-foreground" title={run.credentials?.source?.name}>
                创建者 {run.created_by.username ?? run.created_by.id}
                {run.credentials?.source && (
                  <> · 凭证：{bindingLabel(run.credentials.source)}
                    {run.credentials.source.unavailable && <span className="text-destructive">（已停用或删除，请更换后续作）</span>}</>
                )}
                {run.credentials?.removed && <> · <span className="text-destructive">凭证「{run.credentials.removed.name}」已撤下</span></>}
                <CredentialControl run={run} />
              </p>
            )}
            <PublishControl run={run} />
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
          <Button size="sm" onClick={() => open({ kind: "continue", run })} disabled={!canContinue(run)} id="run-continue">
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
          <Button size="sm" variant="outline" onClick={actions.downloadSource} id="run-download"
            disabled={!run.worker_id || !run.workspace || !!actions.busy}
            title={!run.worker_id || !run.workspace ? "源码尚未准备，暂无可下载的内容" : "下载源码（含 .git，不含依赖目录与 .env）"}>
            <FolderDown /> 下载源码
          </Button>
          <ReportButton run={run} />
          {app?.openable && (
            <Button size="sm" onClick={actions.openApp} id="run-open-app">
              <MonitorPlay /> 打开远程预览
            </Button>
          )}
          <Button size="sm" variant="outline" onClick={actions.preview} disabled={!run.worker_id} id="run-preview">
            <Eye /> 预览产出
          </Button>
          <Button size="sm" variant="outline" onClick={actions.stop} disabled={!canStop(run) || !!actions.busy} id="run-stop">
            <CircleStop /> 停止
          </Button>
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button size="sm" variant="ghost" aria-label="更多操作"><MoreHorizontal /></Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-52">
              <DropdownMenuItem disabled={close.disabled} onSelect={() => open({ kind: "control", run, action: close.action })}>
                {close.action === "close" ? <Power /> : <RotateCcw />} {close.label}
              </DropdownMenuItem>
              {!design && (
                <DropdownMenuItem onSelect={() => open({ kind: "launch", seed: { steps: run.tasks.map((t) => structuredClone(t.spec)) } })}>
                  <ListRestart /> 按此任务列表发起新开发
                </DropdownMenuItem>
              )}
              <DropdownMenuItem onSelect={actions.exportRun}><Download /> 导出 JSON</DropdownMenuItem>
              <DropdownMenuSeparator />
              <DropdownMenuItem variant="destructive" disabled={!canDelete(run)} onSelect={() => open({ kind: "control", run, action: "delete" })}>
                <Trash2 /> 删除会话
                {deleteReason(run) && <small className="ml-1 text-[11px] text-muted-foreground">（{deleteReason(run)}）</small>}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        </div>
        {resumeReason(run) && <p id="resume-hint" className="text-xs text-muted-foreground">{resumeReason(run)}</p>}
        {crewReason && <p id="crew-entry-hint" className="text-xs text-muted-foreground">{crewReason}</p>}
        {workers.find((w) => w.id === run.worker_id)?.scope === "shared" && (
          <p id="shared-environment" className="text-xs text-muted-foreground">共享执行环境：环境内会话的 Agent 互相可读，记忆未分隔。</p>
        )}
      </header>

      {run.attention && run.status !== "waiting_worker" && (
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
          {run.attention && <AlertTitle id="worker-notice-title">{run.attention.title}</AlertTitle>}
          <AlertDescription className="flex flex-wrap items-center gap-3">
            {run.attention
              ? <span id="worker-notice-detail">{[run.attention.detail, run.attention.action].filter(Boolean).join(" ")}</span>
              : "需求已保存。请选择此开发空间的 Worker 后开始执行。"}
            {run.attention?.workers !== 0 && (
              <Button size="sm" variant="outline" onClick={() => open({ kind: "assign", run })} id="assign-worker"
                disabled={!!resumeReason(run)}><UserCog /> 分配 Worker</Button>
            )}
          </AlertDescription>
        </Alert>
      )}
      {run.planning && (
        <section id="planning" className="space-y-2 rounded-xl border border-primary/20 bg-primary/5 p-5">
          <h2 className="text-sm font-semibold">KiroCrew 规划</h2>
          {run.planning.applied ? (
            <>
              {run.planning.intent && <p className="text-sm"><span className="text-muted-foreground">意图：</span>{run.planning.intent}</p>}
              {run.planning.summary && (
                <details open className="text-sm">
                  <summary className="cursor-pointer text-xs font-medium text-primary">补全后的需求</summary>
                  <pre className="mt-2 font-sans text-sm leading-6 whitespace-pre-wrap">{run.planning.summary}</pre>
                </details>
              )}
              <p className="text-xs text-muted-foreground">生成 {run.planning.tasks?.length ?? 0} 个任务，已追加到本 Session。</p>
              {!!run.planning.notes?.length && (
                <ul className="list-disc pl-5 text-xs text-warning">{run.planning.notes.map((note) => <li key={note}>{note}</li>)}</ul>
              )}
            </>
          ) : <p className="text-sm text-muted-foreground">Kiro Agent 正在识别意图、补全需求并拆分任务；规划完成后任务会自动出现在下方。</p>}
        </section>
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
          <TaskList run={run} onContinue={(taskId) => open({ kind: "continue", run, taskId })} />
          <NativeSessionPanel run={run} />
          <RevisionsPanel run={run} />
          <ChangesPanel run={run} />
        </section>
        <aside className="space-y-4">
          {!design && <AppPreviewPanel run={run} onOpen={actions.openApp} />}
          <EventTimeline events={events} stream={stream} />
          <section className="space-y-2 rounded-xl border bg-card p-4 text-xs" id="native-details">
            <h2 className="text-sm font-semibold">Workspace 与原生会话</h2>
            <dl className="space-y-2">
              <div><dt className="text-muted-foreground">Workspace</dt><dd className="font-mono break-all">{run.workspace || "尚未准备"}</dd></div>
              <div><dt className="text-muted-foreground">原生父对话</dt><dd className="font-mono break-all">{run.native_parent || "尚未建立"}</dd></div>
              <div><dt className="text-muted-foreground">来源</dt><dd className="font-mono break-all">{run.source.url ? `${run.source.url}${run.source.ref ? " @ " + run.source.ref : ""}` : "新建项目"}</dd></div>
            </dl>
            <p className="text-muted-foreground">运行与源码由此空间的 Worker 保管。</p>
            <div className="space-y-1 border-t pt-2">
              <p className="text-muted-foreground">需要开发服务器的项目，在本机仓库目录运行后打开 localhost:&lt;远端端口+10000&gt;：</p>
              <button type="button" className="flex w-full items-center gap-2 rounded-md bg-muted px-2 py-1.5 text-left font-mono text-[11px] hover:bg-accent"
                onClick={() => navigator.clipboard?.writeText(`deploy/aws/deploy.sh space ${run.id}`).then(() => toast.success("已复制本地预览命令"))}>
                <span className="min-w-0 flex-1 break-all">deploy/aws/deploy.sh space {run.id}</span><Copy className="size-3.5 shrink-0" />
              </button>
            </div>
          </section>
        </aside>
      </div>
    </div>
  );
}
