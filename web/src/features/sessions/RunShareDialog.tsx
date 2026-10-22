import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/api/client";
import type { Run, RunShare } from "@/api/types";
import { FormDialog } from "@/components/FormDialog";
import { NativeSelect } from "@/components/NativeSelect";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useConsole } from "@/features/console/context";
import { elevated } from "@/lib/access";

const KINDS = { user: "某个人", team: "某个 Team", everyone: "全公司" } as const;
const ROLES = { viewer: "查看", collaborator: "协作" } as const;

/** Session → 共享 (D64): with a person, a Team one is in, or the whole company (viewing only). */
export function RunShareDialog({ run }: { run: Run }) {
  const { close, me, teams, allTeams } = useConsole();
  const client = useQueryClient();
  const key = ["runs", run.id, "shares"];
  const shares = useQuery({ queryKey: key, queryFn: () => api<RunShare[]>(`/runs/${encodeURIComponent(run.id)}/shares`) });
  const choices = (elevated(me) ? allTeams : teams).filter((team) => !team.archived);
  const [kind, setKind] = useState<keyof typeof KINDS>("user");
  const [username, setUsername] = useState("");
  const [team, setTeam] = useState("");
  const [role, setRole] = useState<keyof typeof ROLES>("viewer");
  const shown = kind === "everyone" ? "viewer" : role;
  const ready = kind === "everyone" || (kind === "user" ? !!username.trim() : !!(team || choices[0]?.id));
  const refresh = () => client.invalidateQueries({ queryKey: key });

  const add = async () => {
    const target = kind === "user" ? { username: username.trim() } : kind === "team" ? { id: team || choices[0]?.id } : {};
    try {
      await api(`/runs/${encodeURIComponent(run.id)}/shares`, "POST", { kind, ...target, role: shown });
      setUsername("");
      await refresh();
      toast.success("已共享");
    } catch (error) {
      toast.error((error as Error).message);
    }
  };
  const remove = (share: RunShare) =>
    api(`/runs/${encodeURIComponent(run.id)}/shares/${encodeURIComponent(share.id)}`, "DELETE")
      .then(refresh).then(() => toast.success("已取消共享")).catch((error: Error) => toast.error(error.message));

  return (
    <FormDialog title="共享会话" kicker="SHARE" wide onClose={close}
      description="共享给某个人、你所在的 Team 或全公司。查看：会话详情、时间线、代码改动、报告与预览；协作：再加续作、停止、确认与关闭，与 Team 的普通成员相同。删除、发布、改派执行环境与原生 KiroCrew 入口不对共享开放；全公司只能查看。取消后立即生效。">
      <section className="space-y-2" aria-label="已共享">
        {(shares.data ?? []).length ? (shares.data ?? []).map((share) => (
          <div key={share.id} data-share={share.id} className="flex flex-wrap items-center gap-2 rounded-lg border bg-card px-3 py-2 text-sm">
            <span className="text-xs text-muted-foreground">{KINDS[share.kind]}</span>
            <span className="min-w-0 flex-1 break-words font-medium">{share.name}</span>
            <span className="text-xs text-muted-foreground">{ROLES[share.role]}{share.by ? ` · 由 ${share.by} 共享` : ""}</span>
            <Button type="button" size="sm" variant="ghost" className="text-destructive" onClick={() => remove(share)}>取消共享</Button>
          </div>
        )) : <p className="text-sm text-muted-foreground">{shares.isPending ? "读取中…" : "还没有共享给任何人。"}</p>}
      </section>
      <div className="grid gap-3 border-t pt-4 sm:grid-cols-3">
        <div className="space-y-1.5">
          <Label htmlFor="share-kind">共享给</Label>
          <NativeSelect id="share-kind" value={kind} onChange={(e) => setKind(e.target.value as keyof typeof KINDS)}>
            {Object.entries(KINDS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </NativeSelect>
        </div>
        {kind === "user" && (
          <div className="space-y-1.5">
            <Label htmlFor="share-username">用户名</Label>
            <Input id="share-username" value={username} onChange={(e) => setUsername(e.target.value)} placeholder="对方的平台用户名" />
          </div>
        )}
        {kind === "team" && (
          <div className="space-y-1.5">
            <Label htmlFor="share-team">Team</Label>
            <NativeSelect id="share-team" value={team || choices[0]?.id || ""} onChange={(e) => setTeam(e.target.value)}>
              {choices.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
            </NativeSelect>
          </div>
        )}
        <div className="space-y-1.5">
          <Label htmlFor="share-role">角色</Label>
          <NativeSelect id="share-role" value={shown} disabled={kind === "everyone"}
            onChange={(e) => setRole(e.target.value as keyof typeof ROLES)}>
            {Object.entries(ROLES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </NativeSelect>
        </div>
      </div>
      <div className="flex justify-end">
        <Button type="button" onClick={add} disabled={!ready}>添加共享</Button>
      </div>
    </FormDialog>
  );
}
