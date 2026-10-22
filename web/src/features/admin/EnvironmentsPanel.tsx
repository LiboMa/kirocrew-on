import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { X } from "lucide-react";
import { api } from "@/api/client";
import { keys, useEnvironments } from "@/api/hooks";
import type { Environment, Worker } from "@/api/types";
import { NativeSelect } from "@/components/NativeSelect";
import { Panel } from "@/components/Panel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useConsole } from "@/features/console/context";
import { formatTime } from "@/lib/labels";

/** What a shared execution environment means (D28); shown before an administrator confirms sharing. */
export const SHARED_NOTICE = "环境内所有会话的 Agent 以同一系统账号运行，能互相读取源码、工具登录与记忆；记忆未分隔；只用于同一信任范围。";

function health(environment: Environment) {
  const { health: h, active_runs: active } = environment;
  if (!h.reachable) return `不可达：${h.error}`;
  let origin = "";
  try { origin = h.crew_origin ? " · 原生入口 " + new URL(h.crew_origin).host : ""; } catch { /* not a URL: omitted */ }
  const login = h.tool_auth?.kiro === "expired" ? " · kiro-cli 登录已过期（deploy.sh kiro-login）" : "";
  return `可达${h.version ? " · v" + h.version : ""}${origin} · 进行中会话 ${active}${login}`;
}

/** The single management entry for execution environments (D28): bindings, health, sharing, assignment, enabling. */
export function EnvironmentsPanel() {
  const { workers, allTeams: teams, users, me } = useConsole();
  const client = useQueryClient();
  const [form, setForm] = useState({ name: "", owner: "user:" + me.id, url: "", token: "" });
  const [sharing, setSharing] = useState("");
  const [assign, setAssign] = useState<Record<string, string>>({});
  const environments = new Map((useEnvironments(!!me.admin).data ?? []).map((e) => [e.id, e]));
  const refresh = () => Promise.all([keys.workers, keys.teams, keys.environments].map((queryKey) =>
    client.invalidateQueries({ queryKey })));
  const label = (space: { team_id?: string; user_id?: string }) => space.team_id
    ? "Team · " + (teams.find((t) => t.id === space.team_id)?.name ?? space.team_id)
    : "个人 · " + (users.find((u) => u.id === space.user_id)?.username ?? space.user_id);
  // Teams take one environment each; a user may join a shared one beside a personal Worker.
  const candidates = (w: Worker) => [
    ...teams.filter((t) => !t.environment && !t.archived).map((t) => ({ value: "team:" + t.id, text: "Team · " + t.name })),
    ...(w.scope === "shared" ? users.filter((u) => !w.spaces?.some((s) => s.user_id === u.id))
      .map((u) => ({ value: "user:" + u.id, text: "个人 · " + u.username })) : []),
  ];
  const space = (value: string) => {
    const [kind, id] = value.split(":");
    return kind === "team" ? { team_id: id } : { user_id: id };
  };
  const act = (action: () => Promise<unknown>, done: string) => () =>
    action().then(refresh).then(() => toast.success(done)).catch((e: Error) => toast.error(e.message));
  const owner = (w: (typeof workers)[number]) => w.scope === "shared" ? `共享环境 · 服务 ${w.spaces?.length ?? 0} 个空间`
    : w.scope === "spare" ? "空闲专属环境 · 待分配给 Team"
    : teams.find((t) => t.id === w.team_id)?.name ?? users.find((u) => u.id === w.owner_id)?.username ?? "已分配空间";

  return (
    <Panel title="执行环境" submitLabel="登记 Worker"
      description="每个 Worker 对应一个 KiroCrew Gateway。isolation 字段是部署声明，不能替代实际操作系统隔离。"
      onSubmit={me.admin ? async () => {
        const [kind, id] = form.owner.split(":");
        const connection = { url: form.url, token: form.token };
        await api("/workers", "POST", kind === "spare" ? { name: form.name, spare: true, connection }
          : { name: form.name, [kind === "team" ? "team_id" : "owner_id"]: id, connection });
        await refresh();
        setForm({ ...form, name: "", url: "", token: "" });
        toast.success("Worker 已登记。");
      } : undefined}>
      <div className="space-y-2">
        {workers.map((w) => (
          <div key={w.id} role="group" aria-label={w.name}
            className="flex flex-wrap items-center gap-2 rounded-lg border bg-card px-3 py-2 text-sm">
            <span className="min-w-0 flex-1">{w.name}
              <small className="ml-2 text-xs text-muted-foreground">
                {w.capabilities.isolation === "dedicated" ? "专属环境" : "个人本机"} · {w.enabled ? "可用" : "已停用"} · {owner(w)}
                {typeof w.capabilities.checked_at === "number" && ` · 最近检查 ${formatTime(w.capabilities.checked_at, true)}`}
              </small>
            </span>
            <Button type="button" size="sm" variant="outline" data-refresh-worker={w.id}
              onClick={act(() => api("/workers/" + w.id + "/capabilities"), "已重新读取 Worker 模型目录。")}>刷新模型</Button>
            {!!me.admin && w.scope === "dedicated" && !!w.enabled && (
              <Button type="button" size="sm" variant="outline" data-share-worker={w.id} onClick={() => setSharing(w.id)}>设为共享环境</Button>
            )}
            {!!me.admin && w.scope === "shared" && w.spaces?.length === 1 && !!w.spaces[0].team_id &&
              w.capabilities.isolation === "dedicated" && (
              // A Worker host serving one Team becomes its own again: every member then opens the native entry.
              <Button type="button" size="sm" variant="outline" data-dedicate-worker={w.id}
                onClick={act(() => api("/workers/" + w.id + "/dedicate", "POST", {}), "已改回该 Team 的专属环境。")}>改回专属环境</Button>
            )}
            {!!me.admin && <WorkerTools worker={w} act={act} />}
            {!!me.admin && (
              <Button type="button" size="sm" variant="outline" data-toggle-worker={w.id}
                onClick={act(() => api("/workers/" + w.id + "/enabled", "POST", { enabled: !w.enabled }), w.enabled ? "已停用。" : "已启用。")}>
                {w.enabled ? "停用" : "启用"}
              </Button>
            )}
            {environments.has(w.id) && (
              <p className="w-full text-xs text-muted-foreground" data-environment-health={w.id}>{health(environments.get(w.id)!)}</p>
            )}
            {!!me.admin && w.scope === "shared" && !!w.spaces?.length && (
              <ul className="flex w-full flex-wrap gap-1.5" aria-label="服务的空间">
                {w.spaces.map((s) => (
                  <li key={s.team_id ?? s.user_id} className="flex items-center gap-1 rounded-md bg-muted px-2 py-0.5 text-xs">
                    <span>{label(s)}</span>
                    <button type="button" aria-label={"移出 " + label(s)} className="rounded hover:bg-accent"
                      onClick={act(() => api("/workers/" + w.id + "/spaces", "DELETE", s.team_id ? { team_id: s.team_id }
                        : { user_id: s.user_id }), "已移出。")}><X className="size-3" /></button>
                  </li>
                ))}
              </ul>
            )}
            {!!me.admin && !!w.enabled && (w.scope === "shared" || w.scope === "spare") && (
              <div className="flex w-full items-center gap-2">
                <Label htmlFor={"assign-" + w.id} className="shrink-0 text-xs">分配到</Label>
                <NativeSelect id={"assign-" + w.id} value={assign[w.id] ?? ""}
                  onChange={(e) => setAssign({ ...assign, [w.id]: e.target.value })}>
                  <option value="">{w.scope === "spare" ? "选择 Team" : "选择 Team 或用户"}</option>
                  {candidates(w).map((c) => <option key={c.value} value={c.value}>{c.text}</option>)}
                </NativeSelect>
                <Button type="button" size="sm" variant="outline" disabled={!assign[w.id]}
                  onClick={act(async () => {
                    await api("/workers/" + w.id + "/spaces", "POST", space(assign[w.id]));
                    setAssign({ ...assign, [w.id]: "" });
                  }, "已分配。")}>分配</Button>
              </div>
            )}
            {sharing === w.id && (
              <div role="alert" className="w-full space-y-2 rounded-md border border-warning/40 bg-warning/5 p-3 text-xs">
                <p>设为共享环境后，管理员可以把 Team 和用户的个人空间分配到它上面。{SHARED_NOTICE}
                  共享后只有边界所有者能打开原生 KiroCrew 入口，其他人在会话页的「KiroCrew 会话」中查看。</p>
                <div className="flex gap-2">
                  <Button type="button" size="sm" onClick={act(async () => {
                    await api("/workers/" + w.id + "/share", "POST", { confirm: true });
                    setSharing("");
                  }, "已设为共享环境。")}>确认设为共享环境</Button>
                  <Button type="button" size="sm" variant="ghost" onClick={() => setSharing("")}>取消</Button>
                </div>
              </div>
            )}
          </div>
        ))}
        {!workers.length && <p className="text-sm text-muted-foreground">还没有可见的 Worker。</p>}
      </div>
      {me.admin ? (
        <section className="space-y-3 border-t pt-4">
          <h3 className="text-sm font-semibold">登记已启动的 Worker</h3>
          <p className="text-xs text-muted-foreground">Worker 连接凭据由管理员填写，普通用户不会获得。Gateway 与 Coding 工具的认证保留在 Worker 内。</p>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="worker-name">名称</Label>
              <Input id="worker-name" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="team-a-worker" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="worker-owner">绑定空间</Label>
              <NativeSelect id="worker-owner" value={form.owner} onChange={(e) => setForm({ ...form, owner: e.target.value })}>
                <option value="spare:">空闲专属环境（之后分配给 Team）</option>
                {users.map((u) => <option key={u.id} value={"user:" + u.id}>个人 · {u.username}</option>)}
                {teams.filter((t) => !t.archived).map((t) => <option key={t.id} value={"team:" + t.id}>Team · {t.name}</option>)}
              </NativeSelect>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="worker-url">Worker URL</Label>
              <Input id="worker-url" required value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} placeholder="http://127.0.0.1:8928" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="worker-token">Worker Token</Label>
              <Input id="worker-token" type="password" required minLength={32} autoComplete="off" value={form.token}
                onChange={(e) => setForm({ ...form, token: e.target.value })} />
            </div>
          </div>
        </section>
      ) : <p className="text-sm text-muted-foreground">执行环境由管理员登记和分配。</p>}
    </Panel>
  );
}

type Tool = "name" | "connection" | "retire" | null;

/** Administrator tools on one Worker (spec §6.1): rename, a new connection after a host is replaced, retirement. */
function WorkerTools({ worker, act }: { worker: Worker; act: (action: () => Promise<unknown>, done: string) => () => Promise<unknown> }) {
  const [open, setOpen] = useState<Tool>(null);
  const [title, setTitle] = useState(worker.name);
  const [connection, setConnection] = useState({ url: "", token: "" });
  const base = "/workers/" + encodeURIComponent(worker.id);
  const toggle = (tool: Tool) => setOpen(open === tool ? null : tool);
  return (
    <>
      <Button type="button" size="sm" variant="outline" onClick={() => toggle("name")}>改名</Button>
      <Button type="button" size="sm" variant="outline" onClick={() => toggle("connection")}>更新连接</Button>
      <Button type="button" size="sm" variant="outline" onClick={() => toggle("retire")}>退役</Button>
      {open === "name" && (
        <div className="flex w-full items-end gap-2">
          <div className="flex-1 space-y-1">
            <Label htmlFor={"worker-title-" + worker.id} className="text-xs">Worker 名称</Label>
            <Input id={"worker-title-" + worker.id} value={title} maxLength={80} onChange={(e) => setTitle(e.target.value)} />
          </div>
          <Button type="button" size="sm" disabled={!title.trim()} onClick={() => void act(async () => {
            await api(base, "PATCH", { name: title });
            setOpen(null);
          }, "Worker 已改名。")()}>保存名称</Button>
        </div>
      )}
      {open === "connection" && (
        <div className="w-full space-y-2 rounded-md border px-3 py-2">
          <p className="text-xs text-muted-foreground">用于主机替换或 Token 轮换；新地址按登记时的规则重新核验。执行边界变化（重新部署的主机）后，该环境才可分配给其他 Team。</p>
          <div className="grid gap-2 sm:grid-cols-2">
            <div className="space-y-1">
              <Label htmlFor={"worker-url-" + worker.id} className="text-xs">新的 Worker URL</Label>
              <Input id={"worker-url-" + worker.id} value={connection.url} onChange={(e) => setConnection({ ...connection, url: e.target.value })} />
            </div>
            <div className="space-y-1">
              <Label htmlFor={"worker-token-" + worker.id} className="text-xs">新的 Worker Token</Label>
              <Input id={"worker-token-" + worker.id} type="password" autoComplete="off" minLength={32} value={connection.token}
                onChange={(e) => setConnection({ ...connection, token: e.target.value })} />
            </div>
          </div>
          <Button type="button" size="sm" disabled={!connection.url || !connection.token} onClick={() => void act(async () => {
            await api(base + "/connection", "POST", { connection });
            setConnection({ url: "", token: "" });
            setOpen(null);
          }, "连接已更新。")()}>保存连接</Button>
        </div>
      )}
      {open === "retire" && (
        <div role="alert" className="flex w-full flex-wrap items-center gap-2 rounded-md border border-warning/40 bg-warning/5 px-3 py-2 text-xs">
          <span className="flex-1">退役后不再出现在列表与选择器中，历史会话保留引用；仍是 Team 的执行环境或有进行中的会话时会被拒绝，它服务的个人空间会失去这个环境。</span>
          <Button type="button" size="sm" variant="destructive" onClick={() => { setOpen(null); void act(() => api(base + "/retire", "POST", {}), "已退役。")(); }}>确认退役</Button>
          <Button type="button" size="sm" variant="ghost" onClick={() => setOpen(null)}>取消</Button>
        </div>
      )}
    </>
  );
}
