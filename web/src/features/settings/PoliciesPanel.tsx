import { useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Pencil, Plus, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { keys, usePolicies } from "@/api/hooks";
import type { Policy } from "@/api/types";
import { FormDialog } from "@/components/FormDialog";
import { type Action, ListCard, ListEmpty, RowActions } from "@/components/ListCard";
import { NativeSelect } from "@/components/NativeSelect";
import { Panel } from "@/components/Panel";
import { useRowMotion } from "@/components/rowMotion";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useConsole } from "@/features/console/context";
import { elevated } from "@/lib/access";
import { ROLE_LABELS } from "@/lib/labels";
import { TASK_KIND_LABELS, TOOLS, policyText } from "@/lib/policies";

type Open = { kind: "new" } | { kind: "edit" | "delete"; policy: Policy } | null;

const scopeText = (p: Policy) => (p.scope === "team" ? `Team「${p.team_name ?? p.team_id}」` : "个人");
/** What a mapping preference answers to: 「角色 Evaluator」「类别 审查」 (as policyText begins). */
const targetText = ({ rule }: Policy) =>
  rule.role ? `角色 ${ROLE_LABELS[rule.role] ?? rule.role}` : `类别 ${TASK_KIND_LABELS[rule.kind ?? ""] ?? rule.kind}`;
/** A rule as its row's buttons and the toasts name it; an admission rule by its repositories. */
const ruleName = (p: Policy) => (p.kind === "admission" ? `准入规则 ${p.rule.repos}` : policyText(p));

/** 映射偏好 (D55): the tools one prefers for the planner to weigh. The page holds the rules; adding, editing and deleting
 * one happen in dialogs (PRD 2026-10-23, 管理 / 我的设置 in dialogs). Admission rules are gone (D70: every Session starts
 * once planned); ones written before are listed apart, only to be deleted. */
export function PoliciesPanel() {
  const client = useQueryClient();
  const policies = usePolicies();
  const [dialog, setDialog] = useState<Open>(null);
  const motion = useRowMotion();
  const all = policies.data ?? [];
  const mapping = all.filter((p) => p.kind === "mapping");
  const admission = all.filter((p) => p.kind === "admission");
  const refresh = () => client.invalidateQueries({ queryKey: keys.policies });
  const remove = (p: Policy): Action => ({ icon: Trash2, label: "删除", name: "删除规则：" + ruleName(p), tone: "danger",
    onSelect: () => setDialog({ kind: "delete", policy: p }) });
  const done = (made: Policy | undefined, words: string) => {
    setDialog(null);
    if (made) {
      motion.flash(made.id);
      toast.success(words + "：" + policyText(made));
    }
  };

  return (
    <Panel title="映射偏好"
      description="新会话规划完成后直接开始，不再请人确认。映射偏好告诉 KiroCrew 规划者你希望某类任务或角色用哪个工具，规划者优先采纳、不采纳时写明理由。需求中明确指定的工具总是优先。Team 的偏好由 Team owner 维护。"
      actions={<Button type="button" onClick={() => setDialog({ kind: "new" })}><Plus />新增规则</Button>}>
      <ListCard label="映射偏好列表" onRefresh={() => void policies.refetch()} refreshing={policies.isFetching}
        summary={policies.data ? `共 ${mapping.length} 条` : undefined}>
        {policies.data && !mapping.length ? <ListEmpty>还没有映射偏好：由 KiroCrew 规划者按任务选择工具。点「新增规则」写下你的偏好。</ListEmpty> : (
          <Table>
            <TableHeader>
              <TableRow className="bg-muted/40 hover:bg-muted/40">
                <TableHead className="pl-4">作用域</TableHead><TableHead>对应</TableHead><TableHead>工具</TableHead>
                <TableHead>模型</TableHead><TableHead className="pr-4 text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {mapping.map((p) => {
                const motionProps = motion.props(p.id);
                return (
                  <TableRow key={p.id} data-policy={p.id} data-fresh={motionProps["data-fresh"]} className={motionProps.className}>
                    <TableCell className="pl-4">{scopeText(p)}</TableCell>
                    <TableCell>{targetText(p)}</TableCell>
                    <TableCell><code className="rounded-sm border px-1.5 py-px font-mono text-[11px]">{p.rule.tool}</code></TableCell>
                    <TableCell className="text-muted-foreground">{p.rule.model || "由规划者选择"}</TableCell>
                    <TableCell className="pr-4 text-right">
                      {!p.deletable ? <span className="text-xs text-muted-foreground">由 Team owner 维护</span> : (
                        <RowActions menu={"规则操作：" + ruleName(p)} actions={[
                          { icon: Pencil, label: "编辑", name: "编辑规则：" + ruleName(p), onSelect: () => setDialog({ kind: "edit", policy: p }) },
                          remove(p),
                        ]} />
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </ListCard>
      {admission.length > 0 && (
        <div className="space-y-2">
          <h3 className="text-sm font-medium">已取消的准入规则</h3>
          <p className="text-xs text-muted-foreground">新会话规划完成后直接开始，这些规则不再生效，删除即可。</p>
          <ListCard label="已取消的准入规则">
            <Table>
              <TableHeader>
                <TableRow className="bg-muted/40 hover:bg-muted/40">
                  <TableHead className="pl-4">作用域</TableHead><TableHead>规则</TableHead><TableHead className="pr-4 text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {admission.map((p) => (
                  <TableRow key={p.id} data-policy={p.id} className={motion.props(p.id).className}>
                    <TableCell className="pl-4">{scopeText(p)}</TableCell>
                    <TableCell className="whitespace-normal text-muted-foreground">{policyText(p)}</TableCell>
                    <TableCell className="pr-4 text-right">
                      {p.deletable && <RowActions menu={"规则操作：" + ruleName(p)} actions={[remove(p)]} />}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </ListCard>
        </div>
      )}
      {dialog?.kind === "new" && <RuleDialog onClose={() => setDialog(null)} onSaved={(made) => done(made, "已添加规则")} />}
      {dialog?.kind === "edit" && <RuleDialog policy={dialog.policy} onClose={() => setDialog(null)} onSaved={(made) => done(made, "已保存规则")} />}
      {dialog?.kind === "delete" && (
        <FormDialog title="删除规则" destructive submitLabel="删除规则" onClose={() => setDialog(null)}
          description={dialog.policy.kind === "admission"
            ? <>这条准入规则（{dialog.policy.rule.repos}）已取消、不再生效；删除只是把它从列表里移走。</>
            : <>删除后，规划者不再参考「{policyText(dialog.policy)}」这条偏好，{scopeText(dialog.policy)}的会话改由规划者按任务选择工具。</>}
          onSubmit={async () => {
            const p = dialog.policy;
            await api("/policies/" + encodeURIComponent(p.id), "DELETE");
            setDialog(null);
            await motion.leave(p.id, refresh);
            toast.success("已删除规则：" + ruleName(p));
          }} />
      )}
    </Panel>
  );
}

/** Write a mapping preference — or, given ``policy``, edit one. The API has no PATCH for a rule: saving an edit writes the
 * rule as it now reads, then removes the old one, so a failure leaves at most both (never neither). If only the removal
 * failed, the dialog says so and trying again only removes it. */
function RuleDialog({ policy, onClose, onSaved }: { policy?: Policy; onClose: () => void; onSaved: (made: Policy) => void }) {
  const { me, teams, allTeams } = useConsole();
  const client = useQueryClient();
  const owned = elevated(me) ? allTeams : teams.filter((team) => team.role === "owner");
  const rule = policy?.rule;
  const [scope, setScope] = useState(policy?.scope === "team" ? policy.team_id ?? "personal" : "personal");
  const [by, setBy] = useState<"kind" | "role">(rule?.role ? "role" : "kind");
  const [target, setTarget] = useState(rule?.role ?? rule?.kind ?? "review");
  const [tool, setTool] = useState<string>(rule?.tool ?? "codex");
  const [model, setModel] = useState(rule?.model ?? "");
  const written = useRef<Policy | null>(null);   // an edit's new rule, once written: a retry only removes the old one

  return (
    <FormDialog title={policy ? "编辑规则" : "新增规则"} submitLabel={policy ? "保存规则" : "添加规则"} onClose={onClose}
      description="告诉规划者某类任务或某个角色你希望用哪个工具；需求中明确指定的工具总是优先。"
      onSubmit={async () => {
        const next = { [by]: target, tool, ...(model.trim() ? { model: model.trim() } : {}) };
        const made = written.current ?? await api<Policy>("/policies", "POST", { kind: "mapping",
          scope: scope === "personal" ? "personal" : "team", ...(scope === "personal" ? {} : { team_id: scope }), rule: next });
        if (policy) {
          written.current = made;
          try {
            await api("/policies/" + encodeURIComponent(policy.id), "DELETE");
          } catch (error) {
            await client.invalidateQueries({ queryKey: keys.policies });
            throw new Error("新规则已保存，旧规则没能删除：" + (error as Error).message);
          }
        }
        await client.invalidateQueries({ queryKey: keys.policies });
        onSaved(made);
      }}>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5 sm:col-span-2">
          <Label htmlFor="policy-scope">作用域</Label>
          <NativeSelect id="policy-scope" autoFocus value={scope} onChange={(e) => setScope(e.target.value)}>
            <option value="personal">个人（我的会话）</option>
            {owned.map((team) => <option key={team.id} value={team.id}>Team「{team.name}」</option>)}
          </NativeSelect>
        </div>
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
      </div>
    </FormDialog>
  );
}
