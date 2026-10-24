import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { KeyRound, KeySquare, Plus, Server, ShieldCheck, ShieldOff, Trash2, UserCheck, UserPlus, UserX } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { keys } from "@/api/hooks";
import type { AdminUser, UserToken, Worker } from "@/api/types";
import { FormDialog } from "@/components/FormDialog";
import { ListCard, ListEmpty, RowActions } from "@/components/ListCard";
import { NativeSelect } from "@/components/NativeSelect";
import { Panel } from "@/components/Panel";
import { useRowMotion } from "@/components/rowMotion";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useConsole } from "@/features/console/context";
import { formatTime } from "@/lib/labels";
import { expiresText } from "@/lib/tokens";
import { cn } from "@/lib/utils";

export const ADMIN_USERS = ["admin", "users"] as const;

export const environmentLabel = (w: Pick<Worker, "name" | "scope">) =>
  (w.scope === "shared" ? "共享环境 · " : w.scope === "spare" ? "空闲专属环境 · " : "专属环境 · ") + w.name;

/** The environment a person's own space runs on, as placement picks it: their own dedicated boundary first, else a
 * shared environment serving them; an enabled one before a disabled one. */
export function personalEnvironment(workers: Worker[], user: Pick<AdminUser, "id">): Worker | null {
  const own = workers.filter((w) => (w.scope ?? "dedicated") === "dedicated" && !w.team_id && w.owner_id === user.id);
  const shared = workers.filter((w) => w.scope === "shared" && (w.spaces ?? []).some((s) => s.user_id === user.id));
  const all = [...own, ...shared];
  return all.find((w) => !!w.enabled) ?? all[0] ?? null;
}

const currentLabel = (w: Worker) => (w.enabled ? environmentLabel(w) : "已停用 · " + w.name);
const userPath = (user: AdminUser, rest = "") => "/users/" + encodeURIComponent(user.id) + rest;

/** Refreshes what a change to a person can touch. */
function useRefresh() {
  const client = useQueryClient();
  return () => Promise.all([ADMIN_USERS, keys.users, keys.workers, keys.teams, keys.environments].map((queryKey) =>
    client.invalidateQueries({ queryKey })));
}

type Kind = "reset" | "active" | "admin" | "team" | "environment" | "tokens";
type Open = { kind: "new" } | { kind: Kind; user: AdminUser } | null;
/** A dialog that changed a person hands back the row to light up and what the toast says. */
type Done = (id: string, message: string) => void;
interface DialogProps { user: AdminUser; onClose: () => void; onDone: Done }

/** 管理 › 用户: one row per person with their status, Teams, execution environment, last login and Sessions in progress.
 * The page holds the list; creating someone and every change to a person happen in a dialog, the ones that take
 * something away (停用, 取消管理员) and the ones that hand something over (启用, 设为管理员) saying what follows first
 * (PRD 2026-10-23, 管理 / 我的设置 in dialogs). The common two sit on the row; the rest in its 「⋯」. */
export function UsersPanel() {
  const users = useQuery({ queryKey: ADMIN_USERS, queryFn: () => api<AdminUser[]>("/admin/users") });
  const refresh = useRefresh();
  const [search, setSearch] = useState("");
  const [dialog, setDialog] = useState<Open>(null);
  const motion = useRowMotion();
  const all = users.data ?? [];
  const term = search.trim().toLowerCase();
  const shown = term ? all.filter((u) => [u.username, ...u.teams.map((t) => t.name)].some((x) => x.toLowerCase().includes(term))) : all;
  const close = () => setDialog(null);
  const done: Done = (id, message) => {
    setDialog(null);
    motion.flash(id);
    toast.success(message);
  };

  return (
    <Panel title="用户"
      description="停用或重设密码会让该用户的全部 Token 立即失效；平台至少保留一位有效的管理员，Team 唯一的 owner 不能被停用。"
      actions={<Button type="button" onClick={() => setDialog({ kind: "new" })}><Plus />新建用户</Button>}>
      <ListCard label="用户列表" search={search} onSearch={setSearch} searchLabel="搜索用户名或 Team"
        onRefresh={() => void refresh()} refreshing={users.isFetching}
        summary={users.data ? `${all.filter((u) => u.active).length} 位有效 · 共 ${all.length} 位` : undefined}>
        {!users.data ? (
          <ListEmpty>{users.error ? <span className="text-destructive">读取失败：{(users.error as Error).message}</span> : "读取中…"}</ListEmpty>
        ) : !shown.length ? (
          <ListEmpty>{all.length ? `没有用户名或 Team 含「${search.trim()}」的用户。` : "还没有用户。"}</ListEmpty>
        ) : (
          <Table>
            <TableHeader>
              <TableRow className="bg-muted/40 hover:bg-muted/40">
                <TableHead className="pl-4">用户名</TableHead><TableHead>状态</TableHead><TableHead>所在 Team</TableHead>
                <TableHead>执行环境</TableHead><TableHead>最近登录</TableHead><TableHead className="text-right">进行中的会话</TableHead>
                <TableHead className="pr-4 text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {shown.map((user) => (
                <UserRow key={user.id} user={user} motion={motion.props(user.id)} onOpen={(kind) => setDialog({ kind, user })} />
              ))}
            </TableBody>
          </Table>
        )}
      </ListCard>
      {dialog?.kind === "new" && <NewUserDialog onClose={close} onDone={done} />}
      {dialog?.kind === "reset" && <ResetPasswordDialog user={dialog.user} onClose={close} onDone={done} />}
      {dialog?.kind === "active" && <ActiveDialog user={dialog.user} onClose={close} onDone={done} />}
      {dialog?.kind === "admin" && <AdminDialog user={dialog.user} onClose={close} onDone={done} />}
      {dialog?.kind === "team" && <JoinTeamDialog user={dialog.user} onClose={close} onDone={done} />}
      {dialog?.kind === "environment" && <EnvironmentDialog user={dialog.user} onClose={close} onDone={done} />}
      {dialog?.kind === "tokens" && <TokensDialog user={dialog.user} onClose={close} />}
    </Panel>
  );
}

function UserRow({ user, motion, onOpen }: {
  user: AdminUser;
  motion: { "data-fresh"?: string; className: string };
  onOpen: (kind: Kind) => void;
}) {
  const { me, workers } = useConsole();
  const self = user.id === me.id;   // no locking oneself out from one's own row
  const current = personalEnvironment(workers, user);
  const name = user.username;
  return (
    <TableRow aria-label={name} data-user={user.id} data-fresh={motion["data-fresh"]}
      className={cn(motion.className, !user.active && "text-muted-foreground")}>
      <TableCell className="pl-4">
        <span className="flex items-center gap-2">
          <span className="font-medium">{name}</span>
          {self && <span className="text-xs text-muted-foreground">（你）</span>}
          {user.admin && <span className="rounded-sm bg-primary/10 px-1.5 py-px text-[11px] text-primary">管理员</span>}
        </span>
      </TableCell>
      <TableCell>
        {user.active ? (
          <span className="inline-flex items-center gap-1.5 text-xs"><span aria-hidden className="size-1.5 rounded-full bg-success" />有效</span>
        ) : <span className="rounded-sm bg-destructive/10 px-1.5 py-px text-[11px] text-destructive">已停用</span>}
      </TableCell>
      <TableCell>
        {user.teams.length ? (
          <span className="block max-w-56 truncate" title={user.teams.map((t) => t.name).join("、")}>
            {user.teams.map((t) => t.name + (t.role === "owner" ? "（owner）" : "")).join("、")}
          </span>
        ) : <span className="text-muted-foreground">—</span>}
      </TableCell>
      <TableCell>
        {current ? <span className="block max-w-56 truncate">{currentLabel(current)}</span> : <span className="text-warning">未分配</span>}
      </TableCell>
      <TableCell className="text-muted-foreground tabular-nums">{user.last_login ? formatTime(user.last_login, true) : "从未登录"}</TableCell>
      <TableCell className="text-right tabular-nums">
        {user.running == null ? "—" : user.running ? <span className="font-medium">{user.running}</span>
          : <span className="text-muted-foreground">0</span>}
      </TableCell>
      <TableCell className="pr-4 text-right">
        <RowActions menu={"用户操作：" + name} actions={[
          { icon: KeyRound, label: "重设密码", name: "重设密码：" + name, onSelect: () => onOpen("reset") },
          { icon: UserPlus, label: "加入 Team", name: "加入 Team：" + name, disabled: !user.active, onSelect: () => onOpen("team") },
        ]} more={[
          { icon: Server, label: "改执行环境", name: "改执行环境：" + name, disabled: !user.active, onSelect: () => onOpen("environment") },
          { icon: KeySquare, label: `Token（${user.tokens}）`, name: "Token：" + name, onSelect: () => onOpen("tokens") },
          user.admin
            ? { icon: ShieldOff, label: "取消管理员", name: "取消管理员：" + name, disabled: self, onSelect: () => onOpen("admin") }
            : { icon: ShieldCheck, label: "设为管理员", name: "设为管理员：" + name, onSelect: () => onOpen("admin") },
          user.active
            ? { icon: UserX, label: "停用", name: "停用：" + name, tone: "danger", disabled: self, onSelect: () => onOpen("active") }
            : { icon: UserCheck, label: "启用", name: "启用：" + name, onSelect: () => onOpen("active") },
        ]} />
      </TableCell>
    </TableRow>
  );
}

/** 新建用户: a username and an initial password; the personal space gets the first shared environment unless changed. */
function NewUserDialog({ onClose, onDone }: Omit<DialogProps, "user">) {
  const { workers } = useConsole();
  const refresh = useRefresh();
  const shared = workers.filter((w) => w.scope === "shared" && !!w.enabled);
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [environment, setEnvironment] = useState<string | null>(null);
  const personal = environment ?? shared[0]?.id ?? "";
  return (
    <FormDialog title="新建用户" submitLabel="创建用户" onClose={onClose}
      description="新用户用初始密码登录，之后可以在「我的设置 › 修改密码」自己修改。"
      onSubmit={async () => {
        const name = username.trim();
        if (!name) throw new Error("请填写用户名");
        if (password.length < 12) throw new Error("初始密码至少 12 位");
        const made = await api<{ id: string; username?: string }>("/users", "POST",
          personal ? { username: name, password, worker_id: personal } : { username: name, password });
        await refresh();
        onDone(made.id, "已创建用户 " + (made.username ?? name));
      }}>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="user-name">用户名</Label>
          <Input id="user-name" autoFocus autoComplete="off" value={username} onChange={(e) => setUsername(e.target.value)} />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="user-password">初始密码（至少 12 位）</Label>
          <Input id="user-password" type="password" autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
        </div>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="user-environment">个人空间的执行环境</Label>
        <NativeSelect id="user-environment" value={personal} onChange={(e) => setEnvironment(e.target.value)}>
          <option value="">暂不分配</option>
          {shared.map((w) => <option key={w.id} value={w.id}>{environmentLabel(w)}</option>)}
        </NativeSelect>
        <p className="text-xs text-muted-foreground">个人空间的会话在这里执行；之后可以在用户行的「改执行环境」更换。</p>
      </div>
    </FormDialog>
  );
}

/** 重设密码: the server voids every token of theirs (this login too, when it is one's own row). */
function ResetPasswordDialog({ user, onClose, onDone }: DialogProps) {
  const { me } = useConsole();
  const refresh = useRefresh();
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const name = user.username;
  return (
    <FormDialog title="重设密码" submitLabel="重设密码" onClose={onClose}
      description={<>为 {name} 设一个新密码。重设后，{name} 的全部 Token 立即失效，要用新密码重新登录{user.id === me.id ? "；这是你自己的账户，你也会退出登录" : ""}。</>}
      onSubmit={async () => {
        if (password.length < 12) throw new Error("新密码至少 12 位");
        if (password !== confirm) throw new Error("两次输入不一致");
        await api(userPath(user, "/password"), "POST", { password });
        await refresh();
        onDone(user.id, `已重设 ${name} 的密码`);
      }}>
      <div className="space-y-1.5">
        <Label htmlFor="reset-password">新密码（至少 12 位）</Label>
        <Input id="reset-password" type="password" autoFocus autoComplete="new-password" value={password} onChange={(e) => setPassword(e.target.value)} />
      </div>
      <div className="space-y-1.5">
        <Label htmlFor="reset-confirm">确认新密码</Label>
        <Input id="reset-confirm" type="password" autoComplete="new-password" value={confirm} onChange={(e) => setConfirm(e.target.value)} />
      </div>
    </FormDialog>
  );
}

/** 停用 / 启用, as the server does them: deactivating voids every token and the login at once, and the person's personal
 * repository credentials stop serving Sessions, while the Sessions themselves keep running; enabling gives the login back
 * but not the tokens. A Team's only owner and the last administrator are refused, in the dialog. */
function ActiveDialog({ user, onClose, onDone }: DialogProps) {
  const refresh = useRefresh();
  const name = user.username;
  const deactivate = user.active;
  const running = user.running ?? 0;
  return (
    <FormDialog title={deactivate ? "停用用户" : "启用用户"} submitLabel={deactivate ? "停用用户" : "启用用户"} destructive={deactivate}
      onClose={onClose}
      description={deactivate
        ? <>停用后，{name} 立即不能登录，全部 Token 失效；{running ? `进行中的 ${running} 个会话照常执行` : "已有的会话照常保留"}，但不能再用 {name} 的个人仓库凭证。之后可以重新启用。</>
        : <>启用后，{name} 可以用原来的密码登录；停用时失效的 Token 不会恢复，要重新创建。</>}
      onSubmit={async () => {
        await api(userPath(user, "/active"), "POST", { active: !deactivate });
        await refresh();
        onDone(user.id, (deactivate ? "已停用用户 " : "已启用用户 ") + name);
      }} />
  );
}

/** 设为管理员 / 取消管理员: administration needs the account flag and a token carrying admin, so taking it away is
 * immediate while granting it shows from the next login (security.elevated, issue_token). */
function AdminDialog({ user, onClose, onDone }: DialogProps) {
  const refresh = useRefresh();
  const name = user.username;
  const demote = user.admin;
  return (
    <FormDialog title={demote ? "取消管理员" : "设为管理员"} submitLabel={demote ? "取消管理员" : "设为管理员"} destructive={demote}
      onClose={onClose}
      description={demote
        ? <>取消后，{name} 立即失去平台管理权限，只能使用自己的空间与所在 Team。平台至少保留一位有效的管理员。</>
        : <>设为管理员后，{name} 能管理全部用户、执行环境与 Team，也能查看所有人的会话；{name} 重新登录后生效。</>}
      onSubmit={async () => {
        await api(userPath(user, "/admin"), "POST", { admin: !demote });
        await refresh();
        onDone(user.id, demote ? `已取消 ${name} 的管理员` : `已把 ${name} 设为管理员`);
      }} />
  );
}

/** 加入 Team: as a member, into a Team they are not in yet and that is not archived; roles are changed on the Team page. */
function JoinTeamDialog({ user, onClose, onDone }: DialogProps) {
  const { allTeams } = useConsole();
  const refresh = useRefresh();
  const joinable = allTeams.filter((t) => !t.archived && !user.teams.some((m) => m.id === t.id));
  const [choice, setChoice] = useState<string | null>(null);
  const team = joinable.find((t) => t.id === choice) ?? joinable[0];
  const name = user.username;
  return (
    <FormDialog title="加入 Team" submitLabel="加入 Team" onClose={onClose}
      description={team ? <>把 {name} 作为成员加入一个 Team；角色可以之后在「Team」页修改。</>
        : allTeams.some((t) => !t.archived) ? <>{name} 已在全部 Team 中。</> : <>还没有 Team：先在「Team」页新建一个。</>}
      onSubmit={team && (async () => {
        await api("/teams/" + encodeURIComponent(team.id) + "/members", "POST", { username: name, role: "member" });
        await refresh();
        onDone(user.id, `已把 ${name} 加入 Team「${team.name}」`);
      })}>
      {team && (
        <div className="space-y-1.5">
          <Label htmlFor="join-team">Team</Label>
          <NativeSelect id="join-team" autoFocus value={team.id} onChange={(e) => setChoice(e.target.value)}>
            {joinable.map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
          </NativeSelect>
        </div>
      )}
    </FormDialog>
  );
}

/** 改执行环境: the personal space moves onto the chosen shared environment and off the shared ones it used before. A
 * person on their own dedicated environment keeps it: this only chooses between shared ones. */
function EnvironmentDialog({ user, onClose, onDone }: DialogProps) {
  const { workers } = useConsole();
  const refresh = useRefresh();
  const current = personalEnvironment(workers, user);
  const own = !!current && current.scope !== "shared";
  const options = own ? [] : workers.filter((w) => w.scope === "shared" && !!w.enabled && w.id !== current?.id);
  const [choice, setChoice] = useState<string | null>(null);
  const target = options.find((w) => w.id === choice) ?? options[0];
  const name = user.username;
  const now = current ? <>{name} 的个人空间现在用「{currentLabel(current)}」。</> : <>{name} 的个人空间还没有执行环境。</>;
  return (
    <FormDialog title="改执行环境" submitLabel="改执行环境" onClose={onClose}
      description={own ? <>{now}这里只在共享环境之间更换；专属环境停用时，在「执行环境」页重新启用它。</>
        : !target ? <>{now}没有其他可用的共享环境：先在「执行环境」页把一个 Worker 设为共享环境。</>
        : <>{now}改到另一个共享环境后，新的会话在那里执行；已有的会话派发下一个任务前要重新分配 Worker，已在执行的任务不中断。</>}
      onSubmit={target && (async () => {
        await api("/workers/" + encodeURIComponent(target.id) + "/spaces", "POST", { user_id: user.id });
        for (const w of workers.filter((x) => x.scope === "shared" && x.id !== target.id && (x.spaces ?? []).some((s) => s.user_id === user.id)))
          await api("/workers/" + encodeURIComponent(w.id) + "/spaces", "DELETE", { user_id: user.id });
        await refresh();
        onDone(user.id, `已把 ${name} 的个人空间改到 ${target.name}`);
      })}>
      {target && (
        <div className="space-y-1.5">
          <Label htmlFor="move-environment">改到</Label>
          <NativeSelect id="move-environment" autoFocus value={target.id} onChange={(e) => setChoice(e.target.value)}>
            {options.map((w) => <option key={w.id} value={w.id}>{environmentLabel(w)}</option>)}
          </NativeSelect>
        </div>
      )}
    </FormDialog>
  );
}

/** A person's tokens in use; revoking one asks first in the same dialog, then comes back to the list. */
function TokensDialog({ user, onClose }: { user: AdminUser; onClose: () => void }) {
  const refresh = useRefresh();
  const tokens = useQuery({ queryKey: [...ADMIN_USERS, user.id, "tokens"],
    queryFn: () => api<UserToken[]>(`/admin/users/${encodeURIComponent(user.id)}/tokens`) });
  const [revoking, setRevoking] = useState<UserToken | null>(null);
  const name = user.username;

  if (revoking) {
    return (
      <FormDialog title="撤销 Token" destructive submitLabel="撤销 Token" onClose={() => setRevoking(null)}
        description={<>撤销 {name} 的「{revoking.label}」后，使用它的客户端会立即失去访问。这一步不能撤回。</>}
        onSubmit={async () => {
          await api(`/admin/users/${encodeURIComponent(user.id)}/tokens/${encodeURIComponent(revoking.id)}`, "DELETE");
          await refresh();   // the list here and the row's count
          setRevoking(null);
          toast.success("已撤销 Token " + revoking.label);
        }} />
    );
  }

  return (
    <FormDialog title={name + " 的 Token"} onClose={onClose}
      description={<>{name} 在用的 Token。重设密码或停用会一次让它们全部失效。</>}>
      {tokens.error ? <p className="text-sm text-destructive">{(tokens.error as Error).message}</p>
        : !tokens.data ? <p className="text-sm text-muted-foreground">读取中…</p>
        : !tokens.data.length ? <p className="py-6 text-center text-sm text-muted-foreground">没有有效的 Token。</p> : (
          <ul aria-label={name + " 的 Token"} className="divide-y rounded-md border">
            {tokens.data.map((token) => (
              <li key={token.id} className="flex items-center gap-3 px-3 py-2.5">
                <div className="min-w-0 flex-1 space-y-1">
                  <p className="truncate text-sm font-medium">{token.label}</p>
                  <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground tabular-nums">
                    <span className="flex gap-1">
                      {token.scopes.map((s) => <code key={s} className="rounded-sm border px-1.5 py-px font-mono text-[11px]">{s}</code>)}
                    </span>
                    <span>{expiresText(token.expires)}</span>
                    <span>{token.last_used ? "最近使用 " + new Date(token.last_used * 1000).toLocaleString() : "从未使用"}</span>
                  </p>
                </div>
                <Button type="button" size="xs" variant="ghost" aria-label={"撤销 Token：" + token.label} onClick={() => setRevoking(token)}
                  className="text-muted-foreground hover:bg-destructive/10 hover:text-destructive">
                  <Trash2 />撤销
                </Button>
              </li>
            ))}
          </ul>
        )}
    </FormDialog>
  );
}
