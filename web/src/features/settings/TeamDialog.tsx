import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/api/client";
import { keys } from "@/api/hooks";
import type { Team } from "@/api/types";
import { FormDialog } from "@/components/FormDialog";
import { NativeSelect } from "@/components/NativeSelect";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { useConsole } from "@/features/console/context";

export function TeamDialog() {
  const { teams, users, space, setSpace, me, close } = useConsole();
  const client = useQueryClient();
  const team = teams.find((t) => t.id === space);
  const [teamName, setTeamName] = useState("");
  const [member, setMember] = useState(users[0]?.username ?? "");
  const [role, setRole] = useState("member");
  const [user, setUser] = useState({ username: "", password: "" });
  const [error, setError] = useState("");
  const act = (action: () => Promise<unknown>, done?: string) => async () => {
    setError("");
    try {
      await action();
      await Promise.all([client.invalidateQueries({ queryKey: keys.teams }), client.invalidateQueries({ queryKey: keys.users })]);
      if (done) toast.success(done);
    } catch (reason) {
      setError((reason as Error).message);
    }
  };

  return (
    <FormDialog title="团队与成员" kicker="TEAM ACCESS" onClose={close}>
      <div className="grid items-end gap-3 sm:grid-cols-[1fr_auto]">
        <div className="space-y-1.5">
          <Label htmlFor="team-name">创建 Team</Label>
          <Input id="team-name" value={teamName} onChange={(e) => setTeamName(e.target.value)} placeholder="团队名称" maxLength={80} />
        </div>
        <Button type="button" id="create-team" onClick={act(async () => {
          const created = await api<Team>("/teams", "POST", { name: teamName });
          setTeamName("");
          setSpace(created.id);
        }, "Team 已创建并切换到该空间。")}>创建 Team</Button>
      </div>
      <p className="text-xs text-muted-foreground">所有成员共享 Team 运行；Owner 管理成员。</p>
      <Separator />
      {team ? (
        <section className="space-y-3">
          <h3 className="text-sm font-semibold">{team.name}</h3>
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
            <p className="text-xs text-muted-foreground">用户随后可加入 Team，或拥有自己的私有开发空间。</p>
            <div className="grid gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor="user-name">用户名</Label>
                <Input id="user-name" autoComplete="off" value={user.username} onChange={(e) => setUser({ ...user, username: e.target.value })} />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="user-password">初始密码（至少 12 位）</Label>
                <Input id="user-password" type="password" autoComplete="new-password" value={user.password}
                  onChange={(e) => setUser({ ...user, password: e.target.value })} />
              </div>
            </div>
            <Button type="button" variant="outline" id="create-user" onClick={act(async () => {
              await api("/users", "POST", user);
              setUser({ username: "", password: "" });
            }, "账户已创建。")}>创建用户</Button>
          </section>
        </>
      )}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    </FormDialog>
  );
}
