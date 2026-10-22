import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/api/client";
import { keys, usePolicies } from "@/api/hooks";
import type { Policy } from "@/api/types";
import { FormDialog } from "@/components/FormDialog";
import { NativeSelect } from "@/components/NativeSelect";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useConsole } from "@/features/console/context";
import { elevated } from "@/lib/access";
import { ROLE_LABELS } from "@/lib/labels";
import { TASK_KIND_LABELS, TOOLS, policyText } from "@/lib/policies";

// D70: admission rules are gone (every Session starts once planned); ones written before are listed to be deleted.
const GROUPS: { kind: Policy["kind"]; title: string; empty: string | null }[] = [
  { kind: "mapping", title: "映射偏好", empty: "还没有映射偏好：由 KiroCrew 规划者按任务选择工具。" },
  { kind: "admission", title: "已取消的准入规则", empty: null },
];

function Row({ policy }: { policy: Policy }) {
  const client = useQueryClient();
  return (
    <div data-policy={policy.id} className="flex flex-wrap items-center gap-2 rounded-lg border bg-card px-3 py-2 text-sm">
      <span className="text-xs text-muted-foreground">{policy.scope === "team" ? `Team「${policy.team_name ?? policy.team_id}」` : "个人"}</span>
      <span className="min-w-0 flex-1 break-words">{policyText(policy)}</span>
      {policy.deletable && (
        <Button type="button" size="sm" variant="ghost" className="text-destructive" onClick={() =>
          api("/policies/" + encodeURIComponent(policy.id), "DELETE")
            .then(() => client.invalidateQueries({ queryKey: keys.policies }))
            .catch((error: Error) => toast.error(error.message))}>删除</Button>
      )}
    </div>
  );
}

/** Settings → 映射偏好 (D55): the tools one prefers for the planner to weigh. */
export function PoliciesDialog() {
  const { close, me, teams, allTeams } = useConsole();
  const client = useQueryClient();
  const { data: policies = [] } = usePolicies();
  const owned = elevated(me) ? allTeams : teams.filter((team) => team.role === "owner");
  const [scope, setScope] = useState("personal");
  const [by, setBy] = useState<"kind" | "role">("kind");
  const [target, setTarget] = useState("review");
  const [tool, setTool] = useState<string>("codex");
  const [model, setModel] = useState("");

  return (
    <FormDialog title="映射偏好" kicker="POLICIES" wide onClose={close} submitLabel="添加"
      description="新会话规划完成后直接开始，不再请人确认。映射偏好告诉 KiroCrew 规划者你希望某类任务或角色用哪个工具，规划者优先采纳、不采纳时写明理由。需求中明确指定的工具总是优先。Team 的偏好由 Team owner 维护。"
      onSubmit={async () => {
        const rule = { [by]: target, tool, ...(model.trim() ? { model: model.trim() } : {}) };
        await api("/policies", "POST", { kind: "mapping", scope: scope === "personal" ? "personal" : "team",
          ...(scope === "personal" ? {} : { team_id: scope }), rule });
        setModel("");
        await client.invalidateQueries({ queryKey: keys.policies });
        toast.success("映射偏好已添加");
      }}>
      <div className="space-y-4">
        {GROUPS.map((group) => {
          const rows = policies.filter((p) => p.kind === group.kind);
          if (!rows.length && group.empty === null) return null;
          return (
            <section key={group.kind} className="space-y-2" aria-label={group.title}>
              <h3 className="text-xs font-semibold text-muted-foreground">{group.title}</h3>
              {rows.length ? rows.map((policy) => <Row key={policy.id} policy={policy} />)
                : <p className="text-sm text-muted-foreground">{group.empty}</p>}
            </section>
          );
        })}
      </div>
      <div className="grid gap-3 border-t pt-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="policy-scope">作用域</Label>
          <NativeSelect id="policy-scope" value={scope} onChange={(e) => setScope(e.target.value)}>
            <option value="personal">个人（我的会话）</option>
            {owned.map((team) => <option key={team.id} value={team.id}>Team「{team.name}」</option>)}
          </NativeSelect>
        </div>
        <>
            <div className="space-y-1.5">
              <Label htmlFor="policy-by">对应</Label>
              <NativeSelect id="policy-by" value={by} onChange={(e) => {
                const next = e.target.value as "kind" | "role";
                setBy(next);
                setTarget(next === "kind" ? "review" : "evaluator");
              }}>
                <option value="kind">按任务类别</option>
                <option value="role">按角色</option>
              </NativeSelect>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="policy-target">{by === "kind" ? "任务类别" : "角色"}</Label>
              <NativeSelect id="policy-target" value={target} onChange={(e) => setTarget(e.target.value)}>
                {Object.entries(by === "kind" ? TASK_KIND_LABELS : ROLE_LABELS).map(([value, label]) =>
                  <option key={value} value={value}>{label}</option>)}
              </NativeSelect>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="policy-tool">工具</Label>
              <NativeSelect id="policy-tool" value={tool} onChange={(e) => setTool(e.target.value)}>
                {TOOLS.map((name) => <option key={name} value={name}>{name}</option>)}
              </NativeSelect>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="policy-model">模型（可空）</Label>
              <Input id="policy-model" value={model} onChange={(e) => setModel(e.target.value)} placeholder="留空由规划者选择" />
            </div>
        </>
      </div>
    </FormDialog>
  );
}
