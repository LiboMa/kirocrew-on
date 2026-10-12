import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/api/client";
import { keys } from "@/api/hooks";
import type { Team, Worker } from "@/api/types";
import { FormDialog } from "@/components/FormDialog";
import { NativeSelect } from "@/components/NativeSelect";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { spaceWorkers, useConsole } from "@/features/console/context";

const environmentLabel = (w: Pick<Worker, "name" | "scope">) =>
  (w.scope === "shared" ? "共享环境 · " : w.scope === "spare" ? "空闲专属环境 · " : "专属环境 · ") + w.name;

export function TeamDialog() {
  const { teams, allTeams, users, workers, space, setSpace, me, close } = useConsole();
  const client = useQueryClient();
  const team = teams.find((t) => t.id === space);
  // A Team is created on an execution environment (D28): a shared one, or a spare one that becomes its own.
  // Shared environments first, so a spare one (which a Team keeps for good) is never the default by accident.
  const environments = workers.filter((w) => !!w.enabled && (w.scope === "shared" || w.scope === "spare"))
    .sort((a, b) => Number(a.scope === "spare") - Number(b.scope === "spare"));
  const shared = environments.filter((w) => w.scope === "shared");
  const [teamName, setTeamName] = useState("");
  const [owner, setOwner] = useState(me.username);
  const [environment, setEnvironment] = useState("");
  const [member, setMember] = useState(users[0]?.username ?? "");
  const [role, setRole] = useState("member");
  const [user, setUser] = useState({ username: "", password: "" });
  const [userEnvironment, setUserEnvironment] = useState<string | null>(null);
  const [assignments, setAssignments] = useState<Record<string, string>>({});
  const [error, setError] = useState("");
  const chosen = environments.some((w) => w.id === environment) ? environment : environments[0]?.id ?? "";
  const personal = userEnvironment ?? shared[0]?.id ?? "";
  const act = (action: () => Promise<unknown>, done?: string) => async () => {
    setError("");
    try {
      await action();
      await Promise.all([keys.teams, keys.users, keys.workers].map((queryKey) => client.invalidateQueries({ queryKey })));
      if (done) toast.success(done);
    } catch (reason) {
      setError((reason as Error).message);
    }
  };

  return (
    <FormDialog title="团队与成员" kicker="TEAM ACCESS" onClose={close}>
      {me.admin ? (
        <section className="space-y-3">
          <div className="grid items-end gap-3 sm:grid-cols-3">
            <div className="space-y-1.5">
              <Label htmlFor="team-name">创建 Team</Label>
              <Input id="team-name" value={teamName} onChange={(e) => setTeamName(e.target.value)} placeholder="团队名称" maxLength={80} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="team-owner">Team owner</Label>
              <NativeSelect id="team-owner" value={owner} onChange={(e) => setOwner(e.target.value)}>
                {users.map((u) => <option key={u.id} value={u.username}>{u.username}</option>)}
              </NativeSelect>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="team-environment">执行环境</Label>
              <NativeSelect id="team-environment" value={chosen} disabled={!environments.length}
                onChange={(e) => setEnvironment(e.target.value)}>
                {environments.map((w) => <option key={w.id} value={w.id}>{environmentLabel(w)}</option>)}
              </NativeSelect>
            </div>
          </div>
          <Button type="button" id="create-team" disabled={!chosen} onClick={act(async () => {
            const created = await api<Team>("/teams", "POST", { name: teamName, owner, worker_id: chosen });
            setTeamName("");
            if (created.role) setSpace(created.id);
          }, "Team 已创建。")}>创建 Team</Button>
          <p className="text-xs text-muted-foreground">
            {environments.length
              ? "共享环境内的会话互相可读；空闲专属环境分配后只属于这个 Team，不能再改绑。"
              : "先在主机上开通执行环境，或把一个 Worker 设为共享环境（「执行环境 · Worker」），再创建 Team。"}
          </p>
          <div className="space-y-2">
            {allTeams.map((t) => {
              const pick = environments.some((w) => w.id === assignments[t.id]) ? assignments[t.id] : environments[0]?.id ?? "";
              return (
                <div key={t.id} data-team-environment={t.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border bg-card px-3 py-2 text-sm">
                  <span>{t.name} <small className="ml-2 text-xs text-muted-foreground">
                    {t.environment
                      ? (t.environment.enabled === false ? "执行环境已停用 · " + t.environment.name : environmentLabel(t.environment))
                      : t.environment === null ? <span>未分配执行环境</span> : null}
                  </small></span>
                  {t.environment === null && !!environments.length && (
                    <span className="flex items-center gap-2">
                      <NativeSelect aria-label={`为 ${t.name} 分配的执行环境`} value={pick}
                        onChange={(e) => setAssignments({ ...assignments, [t.id]: e.target.value })}>
                        {environments.map((w) => <option key={w.id} value={w.id}>{environmentLabel(w)}</option>)}
                      </NativeSelect>
                      <Button type="button" size="sm" variant="outline"
                        onClick={act(() => api("/workers/" + pick + "/spaces", "POST", { team_id: t.id }), "已分配执行环境。")}>
                        分配执行环境
                      </Button>
                    </span>
                  )}
                </div>
              );
            })}
          </div>
        </section>
      ) : <p className="text-sm text-muted-foreground">Team 由平台管理员创建，并在创建时分配执行环境。</p>}
      <Separator />
      {team ? (
        <section className="space-y-3">
          <h3 className="text-sm font-semibold">{team.name}</h3>
          {team.environment !== undefined && (
            <p className="text-xs text-muted-foreground">
              执行环境：{team.environment ? environmentLabel(team.environment) : "未分配，提交会被拒绝，请联系平台管理员"}
            </p>
          )}
          {team.members.map((m) => (
            <div key={m.id} className="flex items-center justify-between rounded-lg border bg-card px-3 py-2 text-sm">
              <span>{m.username} <small className="ml-2 text-xs text-muted-foreground">{m.role}</small></span>
              {team.role === "owner" && (
                <Button type="button" size="sm" variant="ghost" className="text-destructive" data-remove-member={m.username}
                  onClick={act(() => api("/teams/" + team.id + "/members", "DELETE", { username: m.username }))}>移除</Button>
              )}
            </div>
          ))}
          {team.role === "owner" && (
            <div className="grid items-end gap-3 sm:grid-cols-[1fr_9rem_auto]">
              <div className="space-y-1.5">
                <Label htmlFor="member-name">添加现有用户</Label>
                <NativeSelect id="member-name" value={member} onChange={(e) => setMember(e.target.value)}>
                  {users.map((u) => <option key={u.id} value={u.username}>{u.username}</option>)}
                </NativeSelect>
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="member-role">角色</Label>
                <NativeSelect id="member-role" value={role} onChange={(e) => setRole(e.target.value)}>
                  <option value="member">Member</option><option value="owner">Owner</option>
                </NativeSelect>
              </div>
              <Button type="button" variant="outline" id="add-member"
                onClick={act(() => api("/teams/" + team.id + "/members", "POST", { username: member, role }))}>添加 / 更新</Button>
            </div>
          )}
        </section>
      ) : <p className="text-sm text-muted-foreground">切换至一个 Team 后，可在这里管理它的成员。</p>}
      {!!me.admin && (
        <>
          <Separator />
          <section className="space-y-3">
            <h3 className="text-sm font-semibold">创建平台账户</h3>
            <p className="text-xs text-muted-foreground">个人空间可以使用共享环境，不必每人一个 Worker；之后也可以在下方列表中分配。</p>
            <div className="grid gap-3 sm:grid-cols-3">
              <div className="space-y-1.5">
                <Label htmlFor="user-name">用户名</Label>
                <Input id="user-name" autoComplete="off" value={user.username} onChange={(e) => setUser({ ...user, username: e.target.value })} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="user-password">初始密码（至少 12 位）</Label>
                <Input id="user-password" type="password" autoComplete="new-password" value={user.password}
                  onChange={(e) => setUser({ ...user, password: e.target.value })} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="user-environment">个人空间的执行环境</Label>
                <NativeSelect id="user-environment" value={personal} onChange={(e) => setUserEnvironment(e.target.value)}>
                  <option value="">暂不分配</option>
                  {shared.map((w) => <option key={w.id} value={w.id}>{environmentLabel(w)}</option>)}
                </NativeSelect>
              </div>
            </div>
            <Button type="button" variant="outline" id="create-user" onClick={act(async () => {
              await api("/users", "POST", personal ? { ...user, worker_id: personal } : user);
              setUser({ username: "", password: "" });
            }, "账户已创建。")}>创建用户</Button>
            <div className="space-y-2">
              {users.map((u) => {
                const current = spaceWorkers(workers, "", u)[0];
                return (
                  <div key={u.id} data-user-environment={u.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border bg-card px-3 py-2 text-sm">
                    <span><span>{u.username}</span> <small className="ml-2 text-xs text-muted-foreground">{current ? environmentLabel(current) : "未分配"}</small></span>
                    {!current && !!shared.length && (
                      <Button type="button" size="sm" variant="outline"
                        onClick={act(() => api("/workers/" + shared[0].id + "/spaces", "POST", { user_id: u.id }), "已分配共享环境。")}>
                        分配共享环境
                      </Button>
                    )}
                  </div>
                );
              })}
            </div>
          </section>
        </>
      )}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    </FormDialog>
  );
}
