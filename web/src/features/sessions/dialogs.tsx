import { useState } from "react";
import { toast } from "sonner";
import { command } from "@/api/client";
import type { Run } from "@/api/types";
import { AgentFields } from "@/components/AgentFields";
import { FormDialog } from "@/components/FormDialog";
import { NativeSelect } from "@/components/NativeSelect";
import { DEFAULT_RECOVERY, RecoveryFields } from "@/components/RecoveryFields";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { spaceWorkers, useConsole } from "@/features/console/context";
import { useRefreshRun } from "@/features/sessions/useSessionActions";

export function ContinueDialog({ run, taskId }: { run: Run; taskId?: string }) {
  const { close, workers } = useConsole();
  const refresh = useRefreshRun();
  const task = run.tasks.find((t) => t.id === taskId) ?? run.tasks.find((t) => t.status !== "accepted") ?? run.tasks[0];
  const [input, setInput] = useState(run.input);
  const [name, setName] = useState(task.spec.name);
  const [prompt, setPrompt] = useState(task.spec.prompt);
  const [agent, setAgent] = useState({ tool: task.spec.tool, model: task.spec.model, effort: task.spec.effort });
  const [recovery, setRecovery] = useState(run.recovery?.policy ?? DEFAULT_RECOVERY);
  const models = workers.find((w) => w.id === run.worker_id)?.capabilities.models;

  return (
    <FormDialog title={"继续此 Session · " + task.spec.name} kicker="CONTINUE IN PLACE" onClose={close} submitLabel="保存并续作"
      description="保留当前 Session ID、Workspace 与上游交接。此 Task 和后续 Task 将重新执行；旧 Attempt 保留为历史证据。"
      note="工具 / 模型改变会创建新的原生执行 Attempt。"
      onSubmit={async () => {
        await command("/runs/" + run.id + "/continue", {
          expected_version: run.version, task_id: task.id, input, recovery,
          execution: { name, prompt, ...agent, effort: agent.tool === "opencode" ? "" : agent.effort },
        });
        close();
        await refresh(run.id);
        toast.success("续作已登记，保留原 Workspace。");
      }}>
      <div className="space-y-1.5">
        <Label htmlFor="continue-input">本次完整需求</Label>
        <Textarea id="continue-input" value={input} onChange={(e) => setInput(e.target.value)} required minLength={3} rows={4} />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="continue-name">任务名称</Label>
        <Input id="continue-name" value={name} onChange={(e) => setName(e.target.value)} required maxLength={80} />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="continue-prompt">任务说明</Label>
        <Textarea id="continue-prompt" value={prompt} onChange={(e) => setPrompt(e.target.value)} required rows={5} />
      </div>
      <AgentFields value={agent} onChange={setAgent} models={models} idPrefix="continue" />
      <RecoveryFields value={recovery} onChange={setRecovery} idPrefix="continue-recovery" />
      <p className="font-mono text-[11px] text-muted-foreground">{run.id} / {task.id}</p>
    </FormDialog>
  );
}

export function AssignWorkerDialog({ run }: { run: Run }) {
  const { close, workers, me } = useConsole();
  const refresh = useRefreshRun();
  const choices = spaceWorkers(workers, run.team_id ?? "", me);
  const [worker, setWorker] = useState(choices[0]?.id ?? "");
  return (
    <FormDialog title="分配执行 Worker" kicker="ASSIGN WORKER" onClose={close} submitLabel="开始执行"
      onSubmit={async () => {
        if (!worker) throw new Error("此开发空间还没有可用的 Worker。");
        await command("/runs/" + run.id + "/assign-worker", { expected_version: run.version, worker_id: worker });
        close();
        await refresh(run.id);
      }}>
      <div className="space-y-1.5">
        <Label htmlFor="assign-choice">开发空间的执行环境</Label>
        <NativeSelect id="assign-choice" value={worker} onChange={(e) => setWorker(e.target.value)} required>
          {!choices.length && <option value="">此空间尚无可用 Worker</option>}
          {choices.map((w) => (
            <option key={w.id} value={w.id}>{w.name} · {w.capabilities.isolation === "dedicated" ? "专属环境" : "个人本机"}</option>
          ))}
        </NativeSelect>
      </div>
      <p className="text-xs text-muted-foreground">未找到可用环境时，请由管理员在“执行环境”中登记该空间的 Worker。</p>
    </FormDialog>
  );
}

const CONTROL = {
  close: { title: "关闭会话", message: "停止未结束的任务，备份并关闭原生会话。之后可重新打开。" },
  reopen: { title: "重新打开", message: "恢复原生会话；选择 Task 续作后开始新一轮执行。" },
  delete: { title: "删除会话", message: "将停止并备份此原生会话，然后删除对话记录。源码与平台审计保留。" },
} as const;

export function ControlDialog({ run, action }: { run: Run; action: "close" | "reopen" | "delete" }) {
  const { close } = useConsole();
  const refresh = useRefreshRun();
  const [confirmation, setConfirmation] = useState("");
  const copy = CONTROL[action];
  return (
    <FormDialog title={copy.title} kicker="SESSION CONTROL" onClose={close} submitLabel={"确认" + copy.title}
      destructive={action === "delete"} note={action === "delete" ? "删除原生对话前先备份；本版保留源码和审计记录。" : undefined}
      onSubmit={async () => {
        if (action === "delete" && confirmation !== run.id) throw new Error("请输入完整 Session ID 确认删除。");
        await command("/runs/" + run.id + "/" + action, {
          expected_version: run.version, ...(action === "delete" ? { confirmation } : {}),
        });
        close();
        await refresh(run.id);
        toast.success("请求已接收；以原生状态确认完成。");
      }}>
      <p className="text-sm leading-6">{copy.message}</p>
      {action === "delete" && (
        <div className="space-y-1.5">
          <p className="font-mono text-xs break-all">{run.id}</p>
          <Label htmlFor="confirm-id">输入完整 Session ID 确认</Label>
          <Input id="confirm-id" value={confirmation} onChange={(e) => setConfirmation(e.target.value)} autoComplete="off" required />
        </div>
      )}
    </FormDialog>
  );
}
