import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/api/client";
import { keys } from "@/api/hooks";
import type { Grant } from "@/api/types";
import { FormDialog } from "@/components/FormDialog";
import { NativeSelect } from "@/components/NativeSelect";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { useConsole } from "@/features/console/context";

const ROLES: [string, string][] = [["viewer", "查看"], ["runner", "查看并运行"], ["editor", "查看、运行和编辑"]];

export function ShareDialog({ flowId }: { flowId: string }) {
  const { workflows, teams, users, me, close } = useConsole();
  const client = useQueryClient();
  const flow = workflows.find((f) => f.id === flowId);
  const candidates = [...teams.filter((t) => !t.archived).map((t) => ["team:" + t.id, "Team · " + t.name]), ...users.filter((u) => u.id !== me.id).map((u) => ["user:" + u.id, "用户 · " + u.username])];
  const [target, setTarget] = useState(candidates[0]?.[0] ?? "");
  const [role, setRole] = useState("runner");
  if (!flow) return null;
  const title = (g: Grant) => g.kind === "team" ? teams.find((t) => t.id === g.principal)?.name ?? g.principal
    : users.find((u) => u.id === g.principal)?.username ?? g.principal;
  const refresh = () => client.invalidateQueries({ queryKey: keys.workflows });

  return (
    <FormDialog title={"共享 · " + flow.name} kicker="SHARE WORKFLOW" onClose={close} submitLabel="添加授权"
      description="只共享工作流定义。查看、运行、编辑逐级包含；共享管理仍由 Owner 负责。Team 成员退出后立即失去该 Team 授权。"
      note="运行选择 Team 空间时，该次运行对 Team 成员可见。"
      onSubmit={async () => {
        if (!target) throw new Error("没有可共享的用户或 Team。");
        const [kind, principal] = target.split(":");
        await api("/workflows/" + flow.id + "/shares", "POST", { kind, principal, role });
        await refresh();
        toast.success("共享授权已更新。");
      }}>
      <div className="space-y-2" id="share-list">
        {flow.grants.length ? flow.grants.map((grant) => (
          <div key={grant.kind + grant.principal} className="flex items-center justify-between rounded-lg border bg-card px-3 py-2 text-sm">
            <span>{title(grant)} <small className="ml-2 text-xs text-muted-foreground">{grant.kind} · {grant.role}</small></span>
            <Button type="button" size="sm" variant="ghost" className="text-destructive" data-revoke
              onClick={() => api("/workflows/" + flow.id + "/shares", "DELETE", grant).then(refresh).catch((e: Error) => toast.error(e.message))}>
              撤销
            </Button>
          </div>
        )) : <p className="text-sm text-muted-foreground">当前为私有工作流。</p>}
      </div>
      <div className="grid gap-3 border-t pt-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="share-target">共享给</Label>
          <NativeSelect id="share-target" value={target} onChange={(e) => setTarget(e.target.value)}>
            {candidates.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </NativeSelect>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="share-role">授予权限</Label>
          <NativeSelect id="share-role" value={role} onChange={(e) => setRole(e.target.value)}>
            {ROLES.map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </NativeSelect>
        </div>
      </div>
    </FormDialog>
  );
}
