import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/api/client";
import { keys } from "@/api/hooks";
import type { AdminUser, UserToken } from "@/api/types";
import { FormDialog } from "@/components/FormDialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useConsole } from "@/features/console/context";

const ADMIN_USERS = ["admin", "users"] as const;
type Pending = { action: "deactivate" | "activate" | "promote" | "demote"; label: string } | null;

/** Platform administrators manage accounts: deactivate or restore, promote or demote, reset a password, revoke tokens. */
export function UsersDialog() {
  const { close } = useConsole();
  const { data: users = [], error } = useQuery({ queryKey: ADMIN_USERS, queryFn: () => api<AdminUser[]>("/admin/users") });
  return (
    <FormDialog title="用户管理" kicker="USERS" onClose={close} wide
      description="停用或重置密码会立即吊销该用户的全部 Token；平台至少保留一位有效的管理员，Team 唯一的 owner 不能被停用。">
      {error && <p className="text-sm text-destructive">{(error as Error).message}</p>}
      <div className="space-y-2">
        {users.map((user) => <UserRow key={user.id} user={user} />)}
      </div>
    </FormDialog>
  );
}

function UserRow({ user }: { user: AdminUser }) {
  const { me } = useConsole();
  const self = user.id === me.id;   // no locking oneself out from one's own row
  const client = useQueryClient();
  const [revoking, setRevoking] = useState<string | null>(null);
  const [pending, setPending] = useState<Pending>(null);
  const [reset, setReset] = useState<{ password: string; confirm: string; error: string } | null>(null);
  const [showTokens, setShowTokens] = useState(false);
  const tokens = useQuery({ queryKey: [...ADMIN_USERS, user.id, "tokens"], enabled: showTokens,
    queryFn: () => api<UserToken[]>(`/admin/users/${encodeURIComponent(user.id)}/tokens`) });
  const refresh = () => Promise.all([ADMIN_USERS, keys.users].map((queryKey) => client.invalidateQueries({ queryKey })));
  const run = (action: () => Promise<unknown>, done: string) =>
    action().then(refresh).then(() => toast.success(done)).catch((e: Error) => toast.error(e.message));
  const base = `/users/${encodeURIComponent(user.id)}`;
  const confirm = () => {
    if (!pending) return;
    const request = pending.action === "deactivate" || pending.action === "activate"
      ? api(base + "/active", "POST", { active: pending.action === "activate" })
      : api(base + "/admin", "POST", { admin: pending.action === "promote" });
    void run(() => request, pending.label + "完成。");
    setPending(null);
  };
  const submitReset = () => {
    if (!reset) return;
    if (reset.password.length < 12) return setReset({ ...reset, error: "新密码至少 12 个字符" });
    if (reset.password !== reset.confirm) return setReset({ ...reset, error: "两次输入不一致" });
    void run(() => api(base + "/password", "POST", { password: reset.password }), "密码已重置，该用户需要重新登录。");
    setReset(null);
  };

  return (
    <div role="group" aria-label={user.username} className="space-y-2 rounded-lg border bg-card px-3 py-2.5 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <strong>{user.username}</strong>
        {user.admin && <span className="rounded bg-primary/10 px-1.5 text-[11px] text-primary">管理员</span>}
        {!user.active && <span className="rounded bg-destructive/10 px-1.5 text-[11px] text-destructive">已停用</span>}
        <small className="text-xs text-muted-foreground">
          {user.last_login ? "最近登录 " + new Date(user.last_login * 1000).toLocaleString() : "从未登录"}
          {" · "}{user.teams.length ? user.teams.map((t) => `${t.name}（${t.role}）`).join("、") : "不在任何 Team"}
          {" · "}{user.tokens} 个有效 Token
        </small>
        <span className="ml-auto flex flex-wrap gap-1.5">
          <Button type="button" size="sm" variant="outline" disabled={self && user.active} title={self ? "不能停用自己" : undefined}
            onClick={() => setPending(user.active ? { action: "deactivate", label: "停用" } : { action: "activate", label: "恢复" })}>
            {user.active ? "停用" : "恢复"}
          </Button>
          <Button type="button" size="sm" variant="outline" disabled={self && user.admin} title={self ? "不能取消自己的管理员" : undefined}
            onClick={() => setPending(user.admin ? { action: "demote", label: "取消管理员" } : { action: "promote", label: "设为管理员" })}>
            {user.admin ? "取消管理员" : "设为管理员"}
          </Button>
          <Button type="button" size="sm" variant="outline" onClick={() => setReset({ password: "", confirm: "", error: "" })}>重置密码</Button>
          <Button type="button" size="sm" variant="ghost" aria-expanded={showTokens} onClick={() => setShowTokens(!showTokens)}>Token</Button>
        </span>
      </div>
      {pending && (
        <div className="flex items-center gap-2 rounded-md border border-warning/40 bg-warning/5 px-3 py-2 text-xs">
          <span className="flex-1">确认{pending.label} {user.username}？{pending.action === "deactivate" && "其全部 Token 会立即失效。"}</span>
          <Button type="button" size="sm" variant="destructive" onClick={confirm}>确认{pending.label}</Button>
          <Button type="button" size="sm" variant="ghost" onClick={() => setPending(null)}>取消</Button>
        </div>
      )}
      {reset && (
        <div className="grid gap-2 rounded-md border px-3 py-2 sm:grid-cols-[1fr_1fr_auto_auto] sm:items-end">
          <div className="space-y-1">
            <Label htmlFor={"reset-" + user.id} className="text-xs">新密码</Label>
            <Input id={"reset-" + user.id} type="password" autoComplete="new-password" value={reset.password}
              onChange={(e) => setReset({ ...reset, password: e.target.value, error: "" })} />
          </div>
          <div className="space-y-1">
            <Label htmlFor={"reset-confirm-" + user.id} className="text-xs">确认新密码</Label>
            <Input id={"reset-confirm-" + user.id} type="password" autoComplete="new-password" value={reset.confirm}
              onChange={(e) => setReset({ ...reset, confirm: e.target.value, error: "" })} />
          </div>
          <Button type="button" size="sm" onClick={submitReset}>确认重置</Button>
          <Button type="button" size="sm" variant="ghost" onClick={() => setReset(null)}>取消</Button>
          {reset.error && <p role="alert" className="text-xs text-destructive sm:col-span-4">{reset.error}</p>}
        </div>
      )}
      {showTokens && (
        <ul className="space-y-1 rounded-md bg-muted/40 px-3 py-2 text-xs" aria-label={user.username + " 的 Token"}>
          {(tokens.data ?? []).map((token) => (
            <li key={token.id} className="flex items-center gap-2">
              <span className="flex-1">{token.label} · {token.scopes.join(" · ")} · {new Date(token.expires * 1000).toLocaleDateString()} 到期 ·{" "}
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
      )}
    </div>
  );
}
