import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { keys } from "@/api/hooks";
import type { Member, Team, Worker } from "@/api/types";
import { NativeSelect } from "@/components/NativeSelect";
import { Panel } from "@/components/Panel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useConsole } from "@/features/console/context";
import { elevated } from "@/lib/access";

const environmentLabel = (w: Pick<Worker, "name" | "scope">) =>
  (w.scope === "shared" ? "共享环境 · " : w.scope === "spare" ? "空闲专属环境 · " : "专属环境 · ") + w.name;

/** Run a Team or Worker change, refresh what it touches and report the server's message on failure. */
function useAct(setError: (message: string) => void) {
  const client = useQueryClient();
  return (action: () => Promise<unknown>, done?: string) => async () => {
    setError("");
    try {
      await action();
      await Promise.all([keys.teams, keys.users, keys.workers].map((queryKey) => client.invalidateQueries({ queryKey })));
      if (done) toast.success(done);
    } catch (reason) {
      setError((reason as Error).message);
    }
  };
}

/** A confirmation strip for an action that is hard to undo, in the style of the user administration. */
function Confirm({ text, label, onConfirm, onCancel }: { text: string; label: string; onConfirm: () => void; onCancel: () => void }) {
  return (
    <div className="flex w-full flex-wrap items-center gap-2 rounded-md border border-warning/40 bg-warning/5 px-3 py-2 text-xs">
      <span className="flex-1">{text}</span>
      <Button type="button" size="sm" variant="destructive" onClick={onConfirm}>{label}</Button>
      <Button type="button" size="sm" variant="ghost" onClick={onCancel}>取消</Button>
    </div>
  );
}

/** Teams: an administrator creates them and manages every one; everyone else the Teams they belong to (spec §5). */
export function TeamPanel() {
  const { teams, allTeams, space, me } = useConsole();
  const admin = elevated(me);
  // An administrator manages every Team (spec §5); everyone else the Teams they belong to.
  const manageable = admin ? allTeams : teams;
  const [picked, setPicked] = useState("");
  const [creating, setCreating] = useState(false);
  const team = manageable.find((t) => t.id === (picked || space)) ?? manageable[0];
  const [error, setError] = useState("");

  return (
    <Panel title="Team"
      description={admin ? "创建 Team 时指定 owner 与执行环境；成员、角色、执行环境与归档都在下面管理。"
        : "Team 由平台管理员创建，并在创建时分配执行环境。"}
      actions={admin && (
        <Button type="button" variant={creating ? "outline" : "default"} aria-expanded={creating} onClick={() => setCreating(!creating)}>
          <Plus /> 新建 Team
        </Button>
      )}>
      {admin && creating && (
        <CreateTeam setError={setError} onCreated={(id) => { setPicked(id); setCreating(false); }} />
      )}
      {team ? (
        <>
          {manageable.length > 1 && (
            <div className="max-w-sm space-y-1.5">
              <Label htmlFor="team-pick">Team</Label>
              <NativeSelect id="team-pick" value={team.id} onChange={(e) => setPicked(e.target.value)}>
                {manageable.map((t) => <option key={t.id} value={t.id}>{t.name}{t.archived ? " · 已归档" : ""}</option>)}
              </NativeSelect>
            </div>
          )}
          <TeamPage key={team.id} team={team} setError={setError} />
        </>
      ) : <p className="text-sm text-muted-foreground">你还不在任何 Team 中。</p>}
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
    </Panel>
  );
}

function CreateTeam({ setError, onCreated }: { setError: (message: string) => void; onCreated: (id: string) => void }) {
  const { users, workers, me, setSpace } = useConsole();
  const act = useAct(setError);
  // A Team is created on an execution environment (D28): a shared one, or a spare one that becomes its own.
  // Shared environments first, so a spare one (which a Team keeps for good) is never the default by accident.
  const environments = workers.filter((w) => !!w.enabled && (w.scope === "shared" || w.scope === "spare"))
    .sort((a, b) => Number(a.scope === "spare") - Number(b.scope === "spare"));
  const [teamName, setTeamName] = useState("");
  const [owner, setOwner] = useState(me.username);
  const [environment, setEnvironment] = useState("");
  const chosen = environments.some((w) => w.id === environment) ? environment : environments[0]?.id ?? "";
  return (
    <section className="space-y-3 rounded-lg border bg-card px-4 py-4" aria-label="新建 Team">
      <div className="grid items-end gap-3 sm:grid-cols-3">
        <div className="space-y-1.5">
          <Label htmlFor="team-name">新 Team 名称</Label>
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
        onCreated(created.id);
      }, "Team 已创建。")}>创建 Team</Button>
      <p className="text-xs text-muted-foreground">
        {environments.length
          ? "共享环境内的会话互相可读；空闲专属环境分配后属于这个 Team。"
          : "先在主机上开通执行环境，或把一个 Worker 设为共享环境（「管理 › 执行环境」），再创建 Team。"}
      </p>
    </section>
  );
}

type Pending = { kind: "environment" | "archive" } | { kind: "remove" | "leave"; member: string } | null;

/** One Team: name, execution environment, members and roles, archiving (spec §5). */
function TeamPage({ team, setError }: { team: Team; setError: (message: string) => void }) {
  const { users, workers, me } = useConsole();
  const act = useAct(setError);
  const admin = elevated(me);
  const manage = admin || team.role === "owner";
  const archived = !!team.archived;
  const base = "/teams/" + encodeURIComponent(team.id);
  const [title, setTitle] = useState(team.name);
  const [pending, setPending] = useState<Pending>(null);
  // Another enabled shared or spare environment; the Team's current one is not offered again.
  const candidates = workers.filter((w) => !!w.enabled && (w.scope === "shared" || w.scope === "spare")
    && w.id !== team.environment?.worker_id);
  const [pick, setPick] = useState("");
  const target = candidates.some((w) => w.id === pick) ? pick : candidates[0]?.id ?? "";
  const outsiders = users.filter((u) => !team.members.some((m) => m.id === u.id));
  const [member, setMember] = useState("");
  const [role, setRole] = useState("member");
  const adding = outsiders.some((u) => u.username === member) ? member : outsiders[0]?.username ?? "";
  const changeEnvironment = act(() => api(base + "/environment", "POST", { worker_id: target }),
    team.environment ? "已更换执行环境。" : "已分配执行环境。");

  return (
    <section className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">{team.name}</h3>
        {team.role && <span className="rounded bg-muted px-1.5 text-[11px] text-muted-foreground">{team.role}</span>}
        {archived && <span className="rounded bg-destructive/10 px-1.5 text-[11px] text-destructive">已归档</span>}
      </div>

      {manage && (
        <div className="flex items-end gap-2">
          <div className="flex-1 space-y-1.5">
            <Label htmlFor="team-title">Team 名称</Label>
            <Input id="team-title" value={title} maxLength={80} onChange={(e) => setTitle(e.target.value)} />
          </div>
          <Button type="button" variant="outline" disabled={!title.trim() || title.trim() === team.name}
            onClick={act(() => api(base, "PATCH", { name: title }), "Team 已改名。")}>改名</Button>
        </div>
      )}

      <div data-team-environment={team.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border bg-card px-3 py-2 text-sm">
        <span>执行环境 <small className="ml-2 text-xs text-muted-foreground">
          {team.environment
            ? (team.environment.enabled === false ? "执行环境已停用 · " + team.environment.name : environmentLabel(team.environment))
            : team.environment === null ? <span>未分配执行环境</span> : null}
        </small></span>
        {admin && !!candidates.length && (
          <span className="flex items-center gap-2">
            <NativeSelect aria-label={`为 ${team.name} 分配的执行环境`} value={target} onChange={(e) => setPick(e.target.value)}>
              {candidates.map((w) => <option key={w.id} value={w.id}>{environmentLabel(w)}</option>)}
            </NativeSelect>
            <Button type="button" size="sm" variant="outline"
              onClick={team.environment ? () => setPending({ kind: "environment" }) : changeEnvironment}>
              {team.environment ? "更换执行环境" : "分配执行环境"}
            </Button>
          </span>
        )}
        {pending?.kind === "environment" && (
          <Confirm label="确认更换" onCancel={() => setPending(null)}
            onConfirm={() => { setPending(null); void changeEnvironment(); }}
            text="旧会话不迁移：下次派发时进入等待，由成员或管理员重新分配；原环境若是该 Team 的专属主机，会改为空闲、只能再分配回这个 Team。确认更换？" />
        )}
      </div>

      <div className="space-y-2">
        {team.members.map((m) => (
          <MemberRow key={m.id} member={m} self={m.id === me.id} manage={manage} pending={pending} setPending={setPending}
            onRole={act(() => api(base + "/members", "POST", { username: m.username, role: m.role === "owner" ? "member" : "owner" }),
              "角色已更新。")}
            onRemove={act(() => api(base + "/members", "DELETE", { username: m.username }),
              m.id === me.id ? "已退出 Team。" : "已移除。")} />
        ))}
      </div>

      {manage && !archived && (
        <div className="grid items-end gap-3 sm:grid-cols-[1fr_9rem_auto]">
          <div className="space-y-1.5">
            <Label htmlFor="member-name">添加现有用户</Label>
            <NativeSelect id="member-name" value={adding} onChange={(e) => setMember(e.target.value)}>
              {outsiders.map((u) => <option key={u.id} value={u.username}>{u.username}</option>)}
            </NativeSelect>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="member-role">角色</Label>
            <NativeSelect id="member-role" value={role} onChange={(e) => setRole(e.target.value)}>
              <option value="member">Member</option><option value="owner">Owner</option>
            </NativeSelect>
          </div>
          <Button type="button" variant="outline" id="add-member" disabled={!adding}
            onClick={act(() => api(base + "/members", "POST", { username: adding, role }), "成员已添加。")}>添加 / 更新</Button>
        </div>
      )}

      {admin && (
        <div className="space-y-2">
          {archived ? (
            <Button type="button" variant="outline" onClick={act(() => api(base + "/archive", "POST", { archived: false }), "Team 已恢复。")}>
              恢复 Team
            </Button>
          ) : (
            <Button type="button" variant="outline" onClick={() => setPending({ kind: "archive" })}>归档 Team</Button>
          )}
          {pending?.kind === "archive" && (
            <Confirm label="确认归档" onCancel={() => setPending(null)}
              onConfirm={() => { setPending(null); void act(() => api(base + "/archive", "POST", { archived: true }), "Team 已归档。")(); }}
              text="归档后不能提交新会话或添加成员；已有会话可查看与停止，执行环境保留，恢复后可继续使用。" />
          )}
        </div>
      )}
    </section>
  );
}

function MemberRow({ member, self, manage, pending, setPending, onRole, onRemove }: {
  member: Member; self: boolean; manage: boolean; pending: Pending; setPending: (p: Pending) => void;
  onRole: () => Promise<void>; onRemove: () => Promise<void>;
}) {
  const kind = self ? "leave" : "remove";
  const asking = pending?.kind === kind && pending.member === member.id;
  return (
    <div role="group" aria-label={member.username} className="flex flex-wrap items-center gap-2 rounded-lg border bg-card px-3 py-2 text-sm">
      <span className="flex-1">{member.username} <small className="ml-2 text-xs text-muted-foreground">{member.role}</small></span>
      {manage && (
        <Button type="button" size="sm" variant="ghost" onClick={() => void onRole()}>
          {member.role === "owner" ? "改为 member" : "设为 owner"}
        </Button>
      )}
      {(self || manage) && (
        <Button type="button" size="sm" variant="ghost" className="text-destructive"
          onClick={() => setPending({ kind, member: member.id })}>{self ? "退出 Team" : "移除"}</Button>
      )}
      {asking && (
        <Confirm label={self ? "确认退出" : "确认移除"} onCancel={() => setPending(null)}
          onConfirm={() => { setPending(null); void onRemove(); }}
          text={self ? "退出后不能再查看这个 Team 的会话；唯一的 owner 不能退出。" : `移除 ${member.username}？其创建的 Team 会话保留在 Team 中。`} />
      )}
    </div>
  );
}
