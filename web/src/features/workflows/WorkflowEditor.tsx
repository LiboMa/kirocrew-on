import { useRef, useState } from "react";
import { Archive, Download, FileUp, Play, Share2, Sparkles } from "lucide-react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/api/client";
import { keys, usePlacement } from "@/api/hooks";
import type { ParsedWorkflow, Step, Workflow } from "@/api/types";
import { FormDialog } from "@/components/FormDialog";
import { TaskEditor } from "@/components/TaskEditor";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useConsole } from "@/features/console/context";
import { downloadJson } from "@/lib/download";

const STARTERS = ["chain", "proof", "fix"];

export function WorkflowEditor({ flow, template = "chain" }: { flow: Workflow | null; template?: string }) {
  const { templates, close, open, space } = useConsole();
  const client = useQueryClient();
  const canEdit = !flow || ["owner", "editor"].includes(flow.role);
  const copy = (id: string) => (templates.find((t) => t.id === id)?.steps ?? []).map((s) => ({ ...s, model: "auto", effort: "" }));
  const [name, setName] = useState(flow?.name ?? templates.find((t) => t.id === template)?.name ?? "");
  const [steps, setSteps] = useState<Step[]>(() => structuredClone(flow?.steps ?? copy(template)));
  const [archiving, setArchiving] = useState(false);
  const file = useRef<HTMLInputElement>(null);
  // The models of where this space's Sessions run (D49: the platform's default), not of the first environment listed
  const placement = usePlacement(space).data;
  const models = (placement?.options.find((o) => o.id === placement.default?.id) ?? placement?.options[0])?.models;
  const refresh = () => client.invalidateQueries({ queryKey: keys.workflows });

  async function importFile(chosen: File) {
    try {
      const result = await api<ParsedWorkflow>("/workflows/parse", "POST", { content: await chosen.text() });
      setSteps(result.steps);
      if (result.name !== "导入的工作流") setName(result.name);
      toast.success(`已导入 ${result.steps.length} 个任务` + (result.notes.length ? `（${result.notes.length} 条规范化说明）` : ""));
    } catch (error) {
      toast.error("导入失败：" + (error as Error).message);
    }
  }

  async function archive() {
    await api("/workflows/" + flow!.id + "/archive", "POST", { expected_version: flow!.version });
    await refresh();
    close();
    toast.success("工作流已归档；已有 Session 和历史版本保留。");
  }

  return (
    <FormDialog wide title={flow ? "编辑工作流" : "构建工作流"} onClose={close} submitLabel="保存"
      kicker={flow ? `WORKFLOW · V${flow.version} · ${flow.role.toUpperCase()}` : "BUILD YOUR WORKFLOW"}
      note="名称在同一 Owner 下唯一。保存后形成新版本；已有运行保留自己的版本。"
      onSubmit={canEdit ? async () => {
        await api("/workflows" + (flow ? "/" + flow.id + "/versions" : ""), "POST",
          { name, steps, ...(flow ? { expected_version: flow.version } : {}) });
        await refresh();
        close();
        toast.success("工作流已保存；新运行使用新版本。");
      } : undefined}
      footer={flow && (
        <Button type="button" variant="ghost" onClick={() => open({ kind: "launch", seed: { workflow_id: flow.id } })} id="launch-flow">
          <Play /> 用此工作流发起
        </Button>
      )}>
      {canEdit && (
        <div className="flex flex-wrap gap-2">
          <Button type="button" size="sm" id="recommend-flow"
            onClick={() => open({ kind: "launch", seed: { mode: "workflow", workflow: flow ? { id: flow.id, version: flow.version } : null, steps, name } })}>
            <Sparkles /> 按需求让 KiroCrew 推荐
          </Button>
          {STARTERS.map((id) => templates.find((t) => t.id === id)).filter((t) => !!t).map((t) => (
            <Button key={t.id} type="button" size="sm" variant="outline" data-template={t.id}
              onClick={() => { setSteps(copy(t.id)); setName(t.name); }}>{t.name}</Button>
          ))}
          <Button type="button" size="sm" variant="outline" onClick={() => file.current?.click()} id="import-flow"><FileUp /> 导入文件</Button>
          <input ref={file} type="file" hidden accept=".json,.yaml,.yml,.md,.markdown,.txt" aria-label="导入工作流文件"
            onChange={(e) => { const chosen = e.target.files?.[0]; e.target.value = ""; if (chosen) void importFile(chosen); }} />
        </div>
      )}
      <div className="space-y-1.5">
        <Label htmlFor="flow-name">工作流名称</Label>
        <Input id="flow-name" value={name} onChange={(e) => setName(e.target.value)} maxLength={80} required disabled={!canEdit} />
      </div>
      <p className="text-xs leading-6 text-muted-foreground">
        先定义任务，再选择工具与模型。显式模型在执行 Worker 校验；当前原生 spawn 不接受含 /、:、[] 的模型 ID。
      </p>
      <TaskEditor tasks={steps} onChange={setSteps} models={models} idPrefix="step" title="任务" readOnly={!canEdit} />
      {flow && (
        <div className="flex flex-wrap gap-2 border-t pt-4">
          <Button type="button" size="sm" variant="outline" id="export-flow"
            onClick={() => downloadJson("workflow-" + flow.id + ".json", { name: flow.name, steps: flow.steps })}><Download /> 导出 JSON</Button>
          {flow.role === "owner" && (
            <>
              <Button type="button" size="sm" variant="outline" id="share-flow" onClick={() => open({ kind: "share", flowId: flow.id })}>
                <Share2 /> 共享
              </Button>
              <Button type="button" size="sm" variant="outline" id="archive-flow" onClick={() => setArchiving(true)} disabled={!!flow.archived}>
                <Archive /> {flow.archived ? "已归档" : "归档"}
              </Button>
            </>
          )}
        </div>
      )}
      {archiving && (
        <div className="space-y-2 rounded-lg border border-destructive/30 bg-destructive/5 p-4 text-sm" role="alertdialog" aria-label="归档工作流">
          <p>归档“{flow!.name}”后不再创建新运行。已有 Session 和历史版本保留。</p>
          <div className="flex gap-2">
            <Button type="button" size="sm" variant="destructive" onClick={() => archive().catch((e: Error) => toast.error(e.message))}>确认归档</Button>
            <Button type="button" size="sm" variant="outline" onClick={() => setArchiving(false)}>取消</Button>
          </div>
        </div>
      )}
    </FormDialog>
  );
}
