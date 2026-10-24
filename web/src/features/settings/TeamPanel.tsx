import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Archive, ArchiveRestore, LogOut, Pencil, Plus, Server, UserCog, UserMinus, UserPlus } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { keys } from "@/api/hooks";
import type { Member, Team, Worker } from "@/api/types";
import { FormDialog } from "@/components/FormDialog";
import { ListCard, ListEmpty, RowActions, type Action } from "@/components/ListCard";
import { NativeSelect } from "@/components/NativeSelect";
import { Panel } from "@/components/Panel";
import { useRowMotion } from "@/components/rowMotion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useConsole } from "@/features/console/context";
import { elevated } from "@/lib/access";
import { cn } from "@/lib/utils";

const environmentLabel = (w: Pick<Worker, "name" | "scope">) =>
  (w.scope === "shared" ? "共享环境 · " : w.scope === "spare" ? "空闲专属环境 · " : "专属环境 · ") + w.name;

/** What a Team may be created on or moved to (D28): an enabled shared environment, or a spare one that becomes its own.
 * Shared ones first, so a spare one (which a Team keeps for good) is never the default by accident. */
const assignable = (workers: Worker[]) => workers.filter((w) => !!w.enabled && (w.scope === "shared" || w.scope === "spare"))
  .sort((a, b) => Number(a.scope === "spare") - Number(b.scope === "spare"));

const teamPath = (team: Pick<Team, "id">) => "/teams/" + encodeURIComponent(team.id);

const ROLES: Record<string, string> = { owner: "Owner", member: "Member" };
const roleLabel = (role: string) => ROLES[role] ?? role;

/** A flat role badge; an owner's is tinted. ``prefix`` says whose role it is where that is not obvious (「你是」). */
function RoleBadge({ role, prefix = "" }: { role: string; prefix?: string }) {
  return role === "owner"
    ? <Badge variant="outline" className="rounded-sm border-primary/30 bg-primary/10 text-primary">{prefix}{roleLabel(role)}</Badge>
    : <Badge variant="secondary" className="rounded-sm">{prefix}{roleLabel(role)}</Badge>;
}

type Motion = ReturnType<typeof useRowMotion>;
type Open = { kind: "rename" | "add" | "environment" | "archive" | "restore" } | { kind: "role" | "remove" | "leave"; member: Member } | null;

/** Refreshes what a Team change touches: the Teams, the users (whose Teams they are) and the Workers (whose spaces). */
function useRefresh() {
  const client = useQueryClient();
  return () => Promise.all([keys.teams, keys.users, keys.workers].map((queryKey) => client.invalidateQueries({ queryKey })));
}

/** Teams: an administrator creates them and manages every one; everyone else the Teams they belong to (spec §5). The page
 * shows one Team and its members; creating, renaming, members, roles, the environment and archiving each happen in a
 * dialog (PRD 2026-10-23, 我的设置 › Team). The switcher between Teams stays on the page: it navigates, it edits nothing. */
export function TeamPanel() {
  const { teams, allTeams, space, me } = useConsole();
  const admin = elevated(me);
  // An administrator manages every Team (spec §5); everyone else the Teams they belong to.
  const manageable = admin ? allTeams : teams;
  const [picked, setPicked] = useState("");
  const [creating, setCreating] = useState(false);
  // Held here, not in the Team's page, so a Team just created is lit up although its page is a new one.
  const motion = useRowMotion();
  const team = manageable.find((t) => t.id === (picked || space)) ?? manageable[0];

  return (
    <Panel title="Team"
      description={admin ? "创建 Team 时指定 owner 与执行环境；成员、角色、执行环境与归档都在下面管理。"
        : "Team 由平台管理员创建，并在创建时分配执行环境。"}
      actions={admin && <Button type="button" onClick={() => setCreating(true)}><Plus />新建 Team</Button>}>
      {team ? (
        <>
          {manageable.length > 1 && (
            <div className="flex flex-wrap items-center gap-2">
              <Label htmlFor="team-pick" className="text-muted-foreground">Team</Label>
              <NativeSelect id="team-pick" className="w-auto min-w-56" value={team.id} onChange={(e) => setPicked(e.target.value)}>
                {manageable.map((t) => <option key={t.id} value={t.id}>{t.name}{t.archived ? " · 已归档" : ""}</option>)}
              </NativeSelect>
              <span className="text-xs text-muted-foreground">共 {manageable.length} 个</span>
            </div>
          )}
          <TeamPage key={team.id} team={team} motion={motion} />
        </>
      ) : (
        <ListCard label="Team 列表">
          <ListEmpty>{admin ? "还没有 Team。点「新建 Team」，指定 owner 与执行环境。" : "你还不在任何 Team 中。"}</ListEmpty>
        </ListCard>
      )}
      {creating && (
        <NewTeamDialog onClose={(made) => {
          setCreating(false);
          if (made) {
            setPicked(made.id);
            motion.flash("team:" + made.id);
            toast.success("已创建 Team " + made.name);
          }
        }} />
      )}
    </Panel>
  );
}

/** Make a Team: its name, its owner and the execution environment it runs on (D28). */
function NewTeamDialog({ onClose }: { onClose: (made?: Team) => void }) {
  const { users, workers, me, setSpace } = useConsole();
  const refresh = useRefresh();
  const environments = assignable(workers);
  const [name, setName] = useState("");
  const [owner, setOwner] = useState(me.username);
  const [environment, setEnvironment] = useState("");
  const chosen = environments.some((w) => w.id === environment) ? environment : environments[0]?.id ?? "";

  // Without an environment there is nothing to create a Team on: the dialog says what to do instead.
  if (!chosen) {
    return (
      <FormDialog title="新建 Team" onClose={() => onClose()}
        description="先在主机上开通执行环境，或把一个 Worker 设为共享环境（「管理 › 执行环境」），再创建 Team。" />
    );
  }

  return (
    <FormDialog title="新建 Team" submitLabel="创建 Team" onClose={() => onClose()}
      description="Team 创建在一个执行环境上：共享环境内的会话互相可读；空闲专属环境分配后属于这个 Team。"
      onSubmit={async () => {
        if (!name.trim()) throw new Error("请填写 Team 名称。");
        const created = await api<Team>("/teams", "POST", { name: name.trim(), owner, worker_id: chosen });
        await refresh();
        if (created.role) setSpace(created.id);
        onClose(created);
      }}>
      <div className="space-y-1.5">
        <Label htmlFor="team-name">Team 名称</Label>
        <Input id="team-name" autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="团队名称" maxLength={80} />
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="team-owner">Team owner</Label>
          <NativeSelect id="team-owner" value={owner} onChange={(e) => setOwner(e.target.value)}>
            {users.map((u) => <option key={u.id} value={u.username}>{u.username}</option>)}
          </NativeSelect>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="team-environment">执行环境</Label>
          <NativeSelect id="team-environment" value={chosen} onChange={(e) => setEnvironment(e.target.value)}>
            {environments.map((w) => <option key={w.id} value={w.id}>{environmentLabel(w)}</option>)}
          </NativeSelect>
        </div>
      </div>
    </FormDialog>
  );
}

/** One Team: a card with its name, state, execution environment and what may be done to it, then its members as a list
 * with each one's role and actions (spec §5). Every change opens a dialog; a change lights its row up once. */
function TeamPage({ team, motion }: { team: Team; motion: Motion }) {
  const { workers, me } = useConsole();
  const refresh = useRefresh();
  const admin = elevated(me);
  const manage = admin || team.role === "owner";
  const archived = !!team.archived;
  const base = teamPath(team);
  const [open, setOpen] = useState<Open>(null);
  // Another enabled shared or spare environment; the Team's current one is not offered again.
  const candidates = assignable(workers).filter((w) => w.id !== team.environment?.worker_id);
  const close = () => setOpen(null);
  const changed = (row: string, message: string) => {
    setOpen(null);
    motion.flash(row);
    toast.success(message);
  };

  // The Team's actions: at most two beside it — new members and renaming, or first assigning an environment to a Team
  // that has none (it cannot run anything), or restoring an archived one; the rest, rarer, in its 「⋯」.
  const rename: Action = { icon: Pencil, label: "改名", name: "改名：" + team.name, onSelect: () => setOpen({ kind: "rename" }) };
  const add: Action = { icon: UserPlus, label: "添加成员", name: "添加成员：" + team.name, onSelect: () => setOpen({ kind: "add" }) };
  const verb = team.environment ? "更换执行环境" : "分配执行环境";
  const environment: Action = { icon: Server, label: verb, name: verb + "：" + team.name, onSelect: () => setOpen({ kind: "environment" }) };
  const archiving: Action = archived
    ? { icon: ArchiveRestore, label: "恢复", name: "恢复：" + team.name, onSelect: () => setOpen({ kind: "restore" }) }
    : { icon: Archive, label: "归档", name: "归档：" + team.name, tone: "danger", onSelect: () => setOpen({ kind: "archive" }) };
  const moving = admin && candidates.length > 0;
  const unassigned = moving && team.environment === null;
  const primary = archived ? (admin ? [archiving] : []) : [...(unassigned ? [environment] : []), ...(manage ? [add, rename] : [])];
  const inline = primary.slice(0, 2);
  const more = [
    ...primary.slice(2), ...(manage && archived ? [rename] : []), ...(moving && (archived || !unassigned) ? [environment] : []),
    ...(admin && !archived ? [archiving] : []),
  ];
  const card = motion.props("team:" + team.id);

  return (
    <div className="space-y-4">
      <div data-team={team.id} data-fresh={card["data-fresh"]}
        className={cn("flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-card px-4 py-3", card.className)}>
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <h3 className="text-base font-semibold">{team.name}</h3>
            {team.role && <RoleBadge role={team.role} prefix="你是 " />}
            {archived && <Badge variant="outline" className="rounded-sm border-destructive/30 bg-destructive/10 text-destructive">已归档</Badge>}
          </div>
          <p data-team-environment={team.id} className="flex items-center gap-1.5 text-xs text-muted-foreground">
            <Server aria-hidden className="size-3.5" />
            {team.environment
              ? (team.environment.enabled === false ? "执行环境已停用 · " + team.environment.name : environmentLabel(team.environment))
              : team.environment === null ? "未分配执行环境" : null}
          </p>
        </div>
        <RowActions menu={"Team 操作：" + team.name} actions={inline} more={more} />
      </div>

      <ListCard label={team.name + " 的成员"} summary={`共 ${team.members.length} 位成员`}>
        {!team.members.length ? (
          <ListEmpty>{manage && !archived ? "还没有成员。点「添加成员」，把现有用户加入这个 Team。" : "这个 Team 还没有成员。"}</ListEmpty>
        ) : (
          <Table>
            <TableHeader>
              <TableRow className="bg-muted/40 hover:bg-muted/40">
                <TableHead className="pl-4">成员</TableHead><TableHead>角色</TableHead><TableHead className="pr-4 text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {team.members.map((m) => {
                const self = m.id === me.id;
                const row = motion.props("member:" + m.id);
                // Anyone may leave; whoever manages the Team changes roles and removes the others.
                const actions: Action[] = [
                  ...(manage ? [{ icon: UserCog, label: "改角色", name: "改角色：" + m.username, onSelect: () => setOpen({ kind: "role", member: m }) }] : []),
                  ...(self ? [{ icon: LogOut, label: "退出 Team", name: "退出 Team：" + team.name, tone: "danger" as const, onSelect: () => setOpen({ kind: "leave", member: m }) }]
                    : manage ? [{ icon: UserMinus, label: "移出", name: "移出：" + m.username, tone: "danger" as const, onSelect: () => setOpen({ kind: "remove", member: m }) }] : []),
                ];
                return (
                  <TableRow key={m.id} data-member-row data-fresh={row["data-fresh"]} className={row.className}>
                    <TableCell className="pl-4">
                      <span className="font-medium">{m.username}</span>
                      {self && <Badge variant="secondary" className="ml-2 rounded-sm">你</Badge>}
                    </TableCell>
                    <TableCell><RoleBadge role={m.role} /></TableCell>
                    <TableCell className="pr-4 text-right"><RowActions menu={"成员操作：" + m.username} actions={actions} /></TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </ListCard>

      {open?.kind === "rename" && (
        <RenameDialog team={team} onClose={(name) => (name ? changed("team:" + team.id, "已将 Team 改名为 " + name) : close())} />
      )}
      {open?.kind === "add" && (
        <AddMemberDialog team={team} onClose={(added) => (added ? changed("member:" + added.id, "已添加成员 " + added.username) : close())} />
      )}
      {open?.kind === "role" && (
        <RoleDialog team={team} member={open.member}
          onClose={(role) => (role ? changed("member:" + open.member.id, `已将 ${open.member.username} 改为 ${roleLabel(role)}`) : close())} />
      )}
      {open?.kind === "environment" && (
        <EnvironmentDialog team={team} verb={verb} candidates={candidates}
          onClose={(done) => (done ? changed("team:" + team.id, `已为 ${team.name} ${verb}`) : close())} />
      )}
      {open?.kind === "archive" && (
        <FormDialog title="归档 Team" destructive submitLabel="归档 Team" onClose={close}
          description={<>「{team.name}」归档后不能提交新会话或添加成员；已有会话可查看与停止，执行环境保留，恢复后可继续使用。</>}
          onSubmit={async () => {
            await api(base + "/archive", "POST", { archived: true });
            await refresh();
            changed("team:" + team.id, "已归档 Team " + team.name);
          }} />
      )}
      {open?.kind === "restore" && (
        <FormDialog title="恢复 Team" submitLabel="恢复 Team" onClose={close}
          description={<>恢复「{team.name}」后，成员可以再提交会话，Owner 可以再添加成员。</>}
          onSubmit={async () => {
            await api(base + "/archive", "POST", { archived: false });
            await refresh();
            changed("team:" + team.id, "已恢复 Team " + team.name);
          }} />
      )}
      {(open?.kind === "remove" || open?.kind === "leave") && (
        <FormDialog destructive onClose={close}
          title={open.kind === "leave" ? "退出 Team" : "移出成员"} submitLabel={open.kind === "leave" ? "退出 Team" : "移出"}
          description={open.kind === "leave"
            ? <>退出「{team.name}」后不能再查看这个 Team 的会话；唯一的 owner 不能退出。</>
            : <>移出后，{open.member.username} 不能再查看「{team.name}」的会话；其创建的 Team 会话保留在 Team 中。</>}
          onSubmit={async () => {
            const { kind, member } = open;
            await api(base + "/members", "DELETE", { username: member.username });
            setOpen(null);
            await motion.leave("member:" + member.id, refresh);
            toast.success(kind === "leave" ? "已退出 Team " + team.name : "已移出 " + member.username);
          }} />
      )}
    </div>
  );
}

/** Rename a Team; its members, Sessions and environment stay as they are. */
function RenameDialog({ team, onClose }: { team: Team; onClose: (name?: string) => void }) {
  const refresh = useRefresh();
  const [name, setName] = useState(team.name);
  return (
    <FormDialog title="给 Team 改名" submitLabel="改名" onClose={() => onClose()}
      description="新名称会出现在开发空间切换与会话列表里；成员、会话与执行环境都不变。"
      onSubmit={async () => {
        const next = name.trim();
        if (!next) throw new Error("请填写 Team 名称。");
        if (next === team.name) return onClose();
        await api(teamPath(team), "PATCH", { name: next });
        await refresh();
        onClose(next);
      }}>
      <div className="space-y-1.5">
        <Label htmlFor="team-title">Team 名称</Label>
        <Input id="team-title" autoFocus value={name} maxLength={80} onChange={(e) => setName(e.target.value)} />
      </div>
    </FormDialog>
  );
}

/** Add an existing user to a Team, as a member or an owner. */
function AddMemberDialog({ team, onClose }: { team: Team; onClose: (added?: { id: string; username: string }) => void }) {
  const { users } = useConsole();
  const refresh = useRefresh();
  const outsiders = users.filter((u) => !team.members.some((m) => m.id === u.id));
  const [member, setMember] = useState("");
  const [role, setRole] = useState("member");
  const adding = outsiders.find((u) => u.username === member) ?? outsiders[0];

  if (!adding) {
    return <FormDialog title="添加成员" onClose={() => onClose()} description="所有现有用户都已在这个 Team 中；新用户由平台管理员创建。" />;
  }

  return (
    <FormDialog title="添加成员" submitLabel="添加成员" onClose={() => onClose()}
      description={<>把一位现有用户加入「{team.name}」：成员能查看并参与这个 Team 的会话，Owner 还能管理成员与角色。</>}
      onSubmit={async () => {
        await api(teamPath(team) + "/members", "POST", { username: adding.username, role });
        await refresh();
        onClose(adding);
      }}>
      <div className="grid gap-3 sm:grid-cols-[1fr_10rem]">
        <div className="space-y-1.5">
          <Label htmlFor="member-name">用户</Label>
          <NativeSelect id="member-name" autoFocus value={adding.username} onChange={(e) => setMember(e.target.value)}>
            {outsiders.map((u) => <option key={u.id} value={u.username}>{u.username}</option>)}
          </NativeSelect>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="member-role">角色</Label>
          <NativeSelect id="member-role" value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="member">Member</option><option value="owner">Owner</option>
          </NativeSelect>
        </div>
      </div>
    </FormDialog>
  );
}

/** Change a member's role. With two roles the other one is chosen already, as the old 「设为 owner」 toggle did; the
 * button says what the member becomes. The server keeps the last owner from being made a member. */
function RoleDialog({ team, member, onClose }: { team: Team; member: Member; onClose: (role?: string) => void }) {
  const refresh = useRefresh();
  const [role, setRole] = useState(member.role === "owner" ? "member" : "owner");
  return (
    <FormDialog title="改角色" submitLabel={"改为 " + roleLabel(role)} onClose={() => onClose()}
      description={<>{member.username} 现在是「{team.name}」的 {roleLabel(member.role)}。Owner 管理成员与角色，Member 查看并参与会话。</>}
      onSubmit={async () => {
        if (role === member.role) return onClose();
        await api(teamPath(team) + "/members", "POST", { username: member.username, role });
        await refresh();
        onClose(role);
      }}>
      <div className="space-y-1.5">
        <Label htmlFor="member-new-role">角色</Label>
        <NativeSelect id="member-new-role" autoFocus value={role} onChange={(e) => setRole(e.target.value)}>
          <option value="owner">Owner</option><option value="member">Member</option>
        </NativeSelect>
      </div>
    </FormDialog>
  );
}

/** Assign a Team its execution environment, or move it to another one (D28); a move leaves old Sessions where they are. */
function EnvironmentDialog({ team, verb, candidates, onClose }: {
  team: Team; verb: string; candidates: Worker[]; onClose: (done?: boolean) => void;
}) {
  const refresh = useRefresh();
  const [pick, setPick] = useState("");
  const target = candidates.some((w) => w.id === pick) ? pick : candidates[0]?.id ?? "";
  return (
    <FormDialog title={verb} submitLabel={verb} onClose={() => onClose()}
      description={team.environment
        ? "旧会话不迁移：下次派发时进入等待，由成员或管理员重新分配；原环境若是该 Team 的专属主机，会改为空闲、只能再分配回这个 Team。"
        : <>「{team.name}」的会话将在这个环境里运行。共享环境内的会话互相可读；空闲专属环境分配后属于这个 Team。</>}
      onSubmit={async () => {
        await api(teamPath(team) + "/environment", "POST", { worker_id: target });
        await refresh();
        onClose(true);
      }}>
      {team.environment && (
        <p className="text-sm text-muted-foreground">现在：{team.environment.enabled === false
          ? "执行环境已停用 · " + team.environment.name : environmentLabel(team.environment)}</p>
      )}
      <div className="space-y-1.5">
        <Label htmlFor="team-environment-pick">执行环境</Label>
        <NativeSelect id="team-environment-pick" autoFocus value={target} onChange={(e) => setPick(e.target.value)}>
          {candidates.map((w) => <option key={w.id} value={w.id}>{environmentLabel(w)}</option>)}
        </NativeSelect>
      </div>
    </FormDialog>
  );
}
