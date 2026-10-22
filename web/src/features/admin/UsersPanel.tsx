import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, MoreHorizontal, Plus, Search } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { keys } from "@/api/hooks";
import type { AdminUser, UserToken, Worker } from "@/api/types";
import { NativeSelect } from "@/components/NativeSelect";
import { Panel } from "@/components/Panel";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuSub, DropdownMenuSubContent,
  DropdownMenuSubTrigger, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useConsole } from "@/features/console/context";
import { formatTime } from "@/lib/labels";
import { expiresText } from "@/lib/tokens";

export const ADMIN_USERS = ["admin", "users"] as const;
const COLUMNS = 7;

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

/** Refreshes what a change to a person can touch. */
function useRefresh() {
  const client = useQueryClient();
  return () => Promise.all([ADMIN_USERS, keys.users, keys.workers, keys.teams, keys.environments].map((queryKey) =>
    client.invalidateQueries({ queryKey })));
}

/** 管理 › 用户: one row per person with their role, Teams, execution environment, last login and Sessions in progress;
 * every change is one step from the row's menu; only deactivating and removing an administrator ask once. */
export function UsersPanel() {
  const { data: users = [], error } = useQuery({ queryKey: ADMIN_USERS, queryFn: () => api<AdminUser[]>("/admin/users") });
  const [query, setQuery] = useState("");
  const [creating, setCreating] = useState(false);
  const words = query.trim().toLowerCase();
  const shown = users.filter((u) => !words || [u.username, ...u.teams.map((t) => t.name)].some((x) => x.toLowerCase().includes(words)));

  return (
    <Panel title="用户"
      description="停用或重置密码会立即吊销该用户的全部 Token；平台至少保留一位有效的管理员，Team 唯一的 owner 不能被停用。"
      actions={
        <Button type="button" variant={creating ? "outline" : "default"} aria-expanded={creating} onClick={() => setCreating(!creating)}>
          <Plus /> 新建用户
        </Button>
      }>
      {creating && <CreateUser onDone={() => setCreating(false)} />}
      <div className="flex flex-wrap items-center gap-3">
        <div className="relative w-full max-w-xs">
          <Search className="pointer-events-none absolute top-2.5 left-2.5 size-4 text-muted-foreground" aria-hidden />
          <Input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="按用户名或 Team 搜索"
            aria-label="搜索用户" className="h-9 bg-card pl-8" />
        </div>
        <span className="text-xs text-muted-foreground">
          {users.filter((u) => u.active).length} 位有效 · 共 {users.length} 位
        </span>
      </div>
      {error && <p className="text-sm text-destructive">{(error as Error).message}</p>}
      <div className="rounded-lg border bg-card">
        <Table aria-label="用户列表">
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="pl-4">用户名</TableHead>
              <TableHead>角色</TableHead>
              <TableHead>所在 Team</TableHead>
              <TableHead>执行环境</TableHead>
              <TableHead>最近登录</TableHead>
              <TableHead className="text-right">运行中的会话</TableHead>
              <TableHead className="w-12"><span className="sr-only">操作</span></TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {shown.map((user) => <UserRow key={user.id} user={user} />)}
            {!shown.length && (
              <TableRow className="hover:bg-transparent">
                <TableCell colSpan={COLUMNS} className="py-6 text-center text-muted-foreground">
                  {users.length ? "没有匹配的用户。" : "还没有用户。"}
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
    </Panel>
  );
}

/** 新建用户: a username and an initial password; the personal space gets the first shared environment unless changed. */
function CreateUser({ onDone }: { onDone: () => void }) {
  const { workers } = useConsole();
  const refresh = useRefresh();
  const shared = workers.filter((w) => w.scope === "shared" && !!w.enabled);
  const [user, setUser] = useState({ username: "", password: "" });
  const [environment, setEnvironment] = useState<string | null>(null);
  const personal = environment ?? shared[0]?.id ?? "";
  const create = () => api("/users", "POST", personal ? { ...user, worker_id: personal } : user)
    .then(refresh)
    .then(() => { toast.success(`已创建 ${user.username}。`); onDone(); })
    .catch((e: Error) => toast.error(e.message));
  return (
    <section aria-label="新建用户" className="space-y-3 rounded-lg border bg-card px-4 py-4">
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
          <NativeSelect id="user-environment" value={personal} onChange={(e) => setEnvironment(e.target.value)}>
            <option value="">暂不分配</option>
            {shared.map((w) => <option key={w.id} value={w.id}>{environmentLabel(w)}</option>)}
          </NativeSelect>
        </div>
      </div>
      <div className="flex items-center gap-2">
        <Button type="button" id="create-user" disabled={!user.username.trim() || !user.password} onClick={() => void create()}>创建用户</Button>
        <Button type="button" variant="ghost" onClick={onDone}>取消</Button>
      </div>
    </section>
  );
}

type Open = "deactivate" | "demote" | "reset" | "tokens" | null;

function UserRow({ user }: { user: AdminUser }) {
  const { me, workers, allTeams } = useConsole();
  const refresh = useRefresh();
  const self = user.id === me.id;   // no locking oneself out from one's own row
  const current = personalEnvironment(workers, user);
  const own = !!current && current.scope !== "shared";
  const shared = workers.filter((w) => w.scope === "shared" && !!w.enabled);
  const joinable = allTeams.filter((t) => !t.archived && !user.teams.some((m) => m.id === t.id));
  const [open, setOpen] = useState<Open>(null);
  const toggle = (next: Open) => setOpen(open === next ? null : next);
  const base = `/users/${encodeURIComponent(user.id)}`;
  const run = (action: () => Promise<unknown>, done: string) =>
    action().then(refresh).then(() => toast.success(done)).catch((e: Error) => toast.error(e.message));
  const setActive = (active: boolean) => run(() => api(base + "/active", "POST", { active }), active ? `已启用 ${user.username}。` : `已停用 ${user.username}。`);
  const setAdmin = (admin: boolean) => run(() => api(base + "/admin", "POST", { admin }),
    admin ? `已把 ${user.username} 设为管理员。` : `已取消 ${user.username} 的管理员。`);
  // The personal space moves: onto the chosen shared environment, off the shared ones it used before.
  const move = (target: Worker) => run(async () => {
    await api("/workers/" + encodeURIComponent(target.id) + "/spaces", "POST", { user_id: user.id });
    for (const w of workers.filter((x) => x.scope === "shared" && x.id !== target.id && (x.spaces ?? []).some((s) => s.user_id === user.id)))
      await api("/workers/" + encodeURIComponent(w.id) + "/spaces", "DELETE", { user_id: user.id });
  }, `${user.username} 的个人空间改用 ${target.name}。`);
  const join = (team: { id: string; name: string }) => run(() => api("/teams/" + encodeURIComponent(team.id) + "/members", "POST",
    { username: user.username, role: "member" }), `已把 ${user.username} 加入 Team「${team.name}」。`);

  return (
    <>
      <TableRow aria-label={user.username} data-user={user.id} className={user.active ? undefined : "text-muted-foreground"}>
        <TableCell className="pl-4">
          <span className="flex items-center gap-2">
            <strong className="font-medium">{user.username}</strong>
            {self && <span className="text-xs text-muted-foreground">（你）</span>}
            {!user.active && <span className="rounded bg-destructive/10 px-1.5 text-[11px] text-destructive">已停用</span>}
          </span>
        </TableCell>
        <TableCell>
          {user.admin ? <span className="rounded bg-primary/10 px-1.5 py-0.5 text-xs text-primary">管理员</span>
            : <span className="text-xs text-muted-foreground">成员</span>}
        </TableCell>
        <TableCell>
          {user.teams.length ? (
            <span className="block max-w-56 truncate" title={user.teams.map((t) => t.name).join("、")}>
              {user.teams.map((t) => t.name + (t.role === "owner" ? "（owner）" : "")).join("、")}
            </span>
          ) : <span className="text-muted-foreground">—</span>}
        </TableCell>
        <TableCell>
          {current ? (
            <span className="block max-w-56 truncate">{current.enabled ? environmentLabel(current) : "已停用 · " + current.name}</span>
          ) : <span className="text-warning">未分配</span>}
        </TableCell>
        <TableCell className="text-xs">
          {user.last_login ? formatTime(user.last_login, true) : <span className="text-muted-foreground">从未登录</span>}
        </TableCell>
        <TableCell className="text-right tabular-nums">
          {user.running == null ? "—" : user.running ? <strong className="font-medium">{user.running}</strong>
            : <span className="text-muted-foreground">0</span>}
        </TableCell>
        <TableCell className="pr-3 text-right">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button type="button" variant="ghost" size="icon-sm" aria-label={`${user.username} 的操作`}><MoreHorizontal /></Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-52">
              <DropdownMenuItem onSelect={() => setOpen("reset")}>重置密码</DropdownMenuItem>
              {user.active && joinable.length ? (
                <DropdownMenuSub>
                  <DropdownMenuSubTrigger>加入 Team</DropdownMenuSubTrigger>
                  <DropdownMenuSubContent className="max-h-72 w-48 overflow-y-auto">
                    {joinable.map((team) => (
                      <DropdownMenuItem key={team.id} onSelect={() => void join(team)}>{team.name}</DropdownMenuItem>
                    ))}
                  </DropdownMenuSubContent>
                </DropdownMenuSub>
              ) : <DropdownMenuItem disabled>加入 Team{user.active ? "（已在全部 Team 中）" : ""}</DropdownMenuItem>}
              {user.active && !own && shared.length ? (
                <DropdownMenuSub>
                  <DropdownMenuSubTrigger>改执行环境</DropdownMenuSubTrigger>
                  <DropdownMenuSubContent className="max-h-72 w-56 overflow-y-auto">
                    {shared.map((w) => (
                      <DropdownMenuItem key={w.id} disabled={w.id === current?.id} onSelect={() => void move(w)}>
                        {w.id === current?.id ? <Check /> : <span className="size-4" aria-hidden />}{environmentLabel(w)}
                      </DropdownMenuItem>
                    ))}
                  </DropdownMenuSubContent>
                </DropdownMenuSub>
              ) : (
                <DropdownMenuItem disabled>改执行环境{own ? "（用自己的专属环境）" : user.active ? "（没有共享环境）" : ""}</DropdownMenuItem>
              )}
              <DropdownMenuItem onSelect={() => toggle("tokens")}>Token（{user.tokens}）</DropdownMenuItem>
              <DropdownMenuSeparator />
              {user.admin ? (
                <DropdownMenuItem disabled={self} onSelect={() => setOpen("demote")}>取消管理员</DropdownMenuItem>
              ) : (
                <DropdownMenuItem onSelect={() => void setAdmin(true)}>设为管理员</DropdownMenuItem>
              )}
              {user.active ? (
                <DropdownMenuItem variant="destructive" disabled={self} onSelect={() => setOpen("deactivate")}>停用</DropdownMenuItem>
              ) : (
                <DropdownMenuItem onSelect={() => void setActive(true)}>启用</DropdownMenuItem>
              )}
            </DropdownMenuContent>
          </DropdownMenu>
        </TableCell>
      </TableRow>
      {open && (
        <TableRow className="bg-muted/30 hover:bg-muted/30">
          <TableCell colSpan={COLUMNS} className="px-4 py-2 whitespace-normal">
            {open === "deactivate" && (
              <Confirm text={`停用 ${user.username}？其全部 Token 会立即失效，进行中的会话继续运行。`} label="确认停用"
                onConfirm={() => { setOpen(null); void setActive(false); }} onCancel={() => setOpen(null)} />
            )}
            {open === "demote" && (
              <Confirm text={`取消 ${user.username} 的管理员？之后只能使用自己的空间与所在 Team。`} label="确认取消管理员"
                onConfirm={() => { setOpen(null); void setAdmin(false); }} onCancel={() => setOpen(null)} />
            )}
            {open === "reset" && (
              <ResetPassword user={user} onDone={() => setOpen(null)}
                onReset={(password) => run(() => api(base + "/password", "POST", { password }), "密码已重置，该用户需要重新登录。")} />
            )}
            {open === "tokens" && <Tokens user={user} run={run} onClose={() => setOpen(null)} />}
          </TableCell>
        </TableRow>
      )}
    </>
  );
}

function Confirm({ text, label, onConfirm, onCancel }: { text: string; label: string; onConfirm: () => void; onCancel: () => void }) {
  return (
    <div role="alert" className="flex flex-wrap items-center gap-2 rounded-md border border-warning/40 bg-warning/5 px-3 py-2 text-xs">
      <span className="flex-1">{text}</span>
      <Button type="button" size="sm" variant="destructive" onClick={onConfirm}>{label}</Button>
      <Button type="button" size="sm" variant="ghost" onClick={onCancel}>取消</Button>
    </div>
  );
}

function ResetPassword({ user, onReset, onDone }: { user: AdminUser; onReset: (password: string) => void; onDone: () => void }) {
  const [form, setForm] = useState({ password: "", confirm: "", error: "" });
  const submit = () => {
    if (form.password.length < 12) return setForm({ ...form, error: "新密码至少 12 个字符" });
    if (form.password !== form.confirm) return setForm({ ...form, error: "两次输入不一致" });
    onReset(form.password);
    onDone();
  };
  return (
    <div className="grid gap-2 sm:grid-cols-[1fr_1fr_auto_auto] sm:items-end">
      <div className="space-y-1">
        <Label htmlFor={"reset-" + user.id} className="text-xs">{user.username} 的新密码</Label>
        <Input id={"reset-" + user.id} type="password" autoComplete="new-password" value={form.password}
          onChange={(e) => setForm({ ...form, password: e.target.value, error: "" })} />
      </div>
      <div className="space-y-1">
        <Label htmlFor={"reset-confirm-" + user.id} className="text-xs">确认新密码</Label>
        <Input id={"reset-confirm-" + user.id} type="password" autoComplete="new-password" value={form.confirm}
          onChange={(e) => setForm({ ...form, confirm: e.target.value, error: "" })} />
      </div>
      <Button type="button" size="sm" onClick={submit}>确认重置</Button>
      <Button type="button" size="sm" variant="ghost" onClick={onDone}>取消</Button>
      {form.error && <p role="alert" className="text-xs text-destructive sm:col-span-4">{form.error}</p>}
    </div>
  );
}

function Tokens({ user, run, onClose }: { user: AdminUser; run: (action: () => Promise<unknown>, done: string) => Promise<unknown>;
  onClose: () => void }) {
  const [revoking, setRevoking] = useState<string | null>(null);
  const tokens = useQuery({ queryKey: [...ADMIN_USERS, user.id, "tokens"],
    queryFn: () => api<UserToken[]>(`/admin/users/${encodeURIComponent(user.id)}/tokens`) });
  return (
    <div className="space-y-1.5">
      <div className="flex items-center justify-between text-xs">
        <span className="font-medium">{user.username} 的 Token</span>
        <Button type="button" size="xs" variant="ghost" onClick={onClose}>收起</Button>
      </div>
      <ul className="space-y-1 text-xs" aria-label={user.username + " 的 Token"}>
        {(tokens.data ?? []).map((token) => (
          <li key={token.id} className="flex flex-wrap items-center gap-2">
            <span className="flex-1">{token.label} · {token.scopes.join(" · ")} · {expiresText(token.expires)} ·{" "}
              {token.last_used ? "最近使用 " + new Date(token.last_used * 1000).toLocaleString() : "从未使用"}</span>
            {revoking === token.id ? (
              <>
                <Button type="button" size="sm" variant="destructive" onClick={() => {
                  setRevoking(null);
                  void run(() => api(`/admin/users/${encodeURIComponent(user.id)}/tokens/${encodeURIComponent(token.id)}`,
                    "DELETE"), "已吊销。");
                }}>确认吊销</Button>
                <Button type="button" size="sm" variant="ghost" onClick={() => setRevoking(null)}>取消</Button>
              </>
            ) : (
              <Button type="button" size="sm" variant="ghost" aria-label={"吊销 " + token.label}
                onClick={() => setRevoking(token.id)}>吊销</Button>
            )}
          </li>
        ))}
        {tokens.data && !tokens.data.length && <li className="text-muted-foreground">没有有效的 Token。</li>}
      </ul>
    </div>
  );
}
