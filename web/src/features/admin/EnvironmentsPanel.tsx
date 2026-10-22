import { useState, type FormEvent } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Plus, RefreshCw, X } from "lucide-react";
import { api } from "@/api/client";
import { keys, useEnvironments } from "@/api/hooks";
import type { Environment, Tool, Worker } from "@/api/types";
import { NativeSelect } from "@/components/NativeSelect";
import { Panel } from "@/components/Panel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useConsole } from "@/features/console/context";
import { formatTime, TOOLS } from "@/lib/labels";
import { cn } from "@/lib/utils";

/** What a shared execution environment means (D28); shown before an administrator confirms sharing. */
export const SHARED_NOTICE = "环境内所有会话的 Agent 以同一系统账号运行，能互相读取源码、工具登录与记忆；记忆未分隔；只用于同一信任范围。";

type Act = (action: () => Promise<unknown>, done: string) => () => Promise<unknown>;

/** One line of an environment's live health, as the platform last probed it. */
export function healthText(environment: Environment) {
  const { health: h, active_runs: active } = environment;
  if (!h.reachable) return `不可达：${h.error}`;
  let origin = "";
  try { origin = h.crew_origin ? " · 原生入口 " + new URL(h.crew_origin).host : ""; } catch { /* not a URL: omitted */ }
  const login = h.tool_auth?.kiro === "expired" ? " · kiro-cli 登录已过期（deploy.sh kiro-login）" : "";
  return `可达${h.version ? " · v" + h.version : ""}${origin} · 进行中会话 ${active}${login}`;
}

export const scopeLabel = (w: Pick<Worker, "scope" | "capabilities">) =>
  w.scope === "shared" ? "共享" : w.scope === "spare" ? "空闲专属" : w.capabilities.isolation === "dedicated" ? "专属" : "个人本机";

type Status = "ok" | "down" | "disabled" | "unknown";
const STATUS: Record<Status, { text: string; dot: string }> = {
  ok: { text: "可达", dot: "bg-success" },
  down: { text: "不可达", dot: "bg-destructive" },
  disabled: { text: "已停用", dot: "bg-muted-foreground/40" },
  unknown: { text: "检查中", dot: "bg-muted-foreground/40" },
};

export function status(worker: Worker, environment: Environment | undefined): Status {
  if (!worker.enabled) return "disabled";
  if (!environment) return "unknown";
  return environment.health.reachable ? "ok" : "down";
}

/** kiro-cli's login on the boundary: the live probe first, else what the Worker last reported; unknown when unreachable. */
export function kiroLogin(worker: Worker, environment: Environment | undefined): "ok" | "expired" | "unknown" | null {
  const health = environment?.health;
  if (health && !health.reachable) return null;   // not known while it cannot be reached
  const tools = worker.capabilities.tools as Record<string, { login?: string }> | undefined;
  const value = health?.tool_auth?.kiro ?? tools?.kiro?.login;
  return value === "ok" || value === "expired" || value === "unknown" ? value : null;
}

/** The Coding tools that can start on the boundary (D38 §3.1); null when the Worker does not report them. */
export function availableTools(worker: Worker): string[] | null {
  const tools = worker.capabilities.tools as Record<string, { available?: boolean }> | undefined;
  if (!tools || typeof tools !== "object") return null;
  const order = Object.keys(TOOLS);
  return Object.keys(tools).sort((a, b) => (order.indexOf(a) + 1 || 99) - (order.indexOf(b) + 1 || 99))
    .filter((tool) => tools[tool]?.available).map((tool) => TOOLS[tool as Tool] ?? tool);
}

/** An environment that needs an administrator: unreachable, or kiro-cli signed out. */
export const troubled = (worker: Worker, environment: Environment | undefined) =>
  status(worker, environment) === "down" || kiroLogin(worker, environment) === "expired";

function StatusDot({ value }: { value: Status }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs">
      <span className={cn("size-2 shrink-0 rounded-full", STATUS[value].dot)} aria-hidden />
      {STATUS[value].text}
    </span>
  );
}

const LOGIN = { ok: "已登录", expired: "已过期", unknown: "未知" } as const;

/** 管理 › 执行环境 (D28): every environment with its live status; the chosen one's detail and actions below. */
export function EnvironmentsPanel() {
  const { workers } = useConsole();
  const client = useQueryClient();
  const query = useEnvironments(true);
  const environments = new Map((query.data ?? []).map((e) => [e.id, e]));
  const [picked, setPicked] = useState("");
  const [registering, setRegistering] = useState(false);
  // An environment that needs attention is shown first; otherwise the first one.
  const chosen = workers.find((w) => w.id === picked) ?? workers.find((w) => troubled(w, environments.get(w.id))) ?? workers[0];
  const refresh = () => Promise.all([keys.workers, keys.teams, keys.environments].map((queryKey) => client.invalidateQueries({ queryKey })));
  const act: Act = (action, done) => () => action().then(refresh).then(() => toast.success(done)).catch((e: Error) => toast.error(e.message));

  return (
    <Panel title="执行环境"
      description="每个 Worker 对应一个 KiroCrew Gateway。状态每 30 秒自动读取一次；isolation 字段是部署声明，不能替代实际操作系统隔离。"
      actions={<>
        <Button type="button" variant="outline" disabled={query.isFetching} onClick={() => void refresh()}>
          <RefreshCw className={cn(query.isFetching && "animate-spin")} /> 刷新状态
        </Button>
        <Button type="button" variant={registering ? "outline" : "default"} aria-expanded={registering}
          onClick={() => setRegistering(!registering)}><Plus /> 登记 Worker</Button>
      </>}>
      {registering && <RegisterWorker onDone={() => setRegistering(false)} refresh={refresh} />}
      {query.error && <p className="text-sm text-destructive">状态读取失败：{query.error.message}</p>}
      <div className="rounded-lg border bg-card">
        <Table aria-label="执行环境列表">
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              <TableHead className="pl-4">状态</TableHead>
              <TableHead>名称</TableHead>
              <TableHead>类型</TableHead>
              <TableHead>版本</TableHead>
              <TableHead>Kiro 登录</TableHead>
              <TableHead>可用工具</TableHead>
              <TableHead className="text-right">服务空间</TableHead>
              <TableHead className="pr-4 text-right">进行中会话</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {workers.map((w) => {
              const environment = environments.get(w.id);
              const login = kiroLogin(w, environment);
              const tools = availableTools(w);
              const selected = w.id === chosen?.id;
              return (
                <TableRow key={w.id} aria-label={w.name} data-state={selected ? "selected" : undefined}
                  className="cursor-pointer" onClick={() => setPicked(w.id)}>
                  <TableCell className="pl-4"><StatusDot value={status(w, environment)} /></TableCell>
                  <TableCell>
                    <button type="button" aria-pressed={selected} onClick={(e) => { e.stopPropagation(); setPicked(w.id); }}
                      className="rounded-sm font-medium outline-none hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50">
                      {w.name}
                    </button>
                  </TableCell>
                  <TableCell className="text-xs">{scopeLabel(w)}</TableCell>
                  <TableCell className="text-xs">
                    {environment?.health.reachable && environment.health.version ? "v" + environment.health.version
                      : <span className="text-muted-foreground">—</span>}
                  </TableCell>
                  <TableCell className={cn("text-xs", login === "expired" && "font-medium text-destructive")}>
                    {login ? LOGIN[login] : <span className="text-muted-foreground">—</span>}
                  </TableCell>
                  <TableCell className="min-w-40 text-xs whitespace-normal">
                    {tools?.length ? tools.join(" · ") : <span className="text-muted-foreground">{tools ? "没有可用工具" : "—"}</span>}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {w.scope === "shared" ? w.spaces?.length ?? 0 : <span className="text-muted-foreground">—</span>}
                  </TableCell>
                  <TableCell className="pr-4 text-right tabular-nums">{environment ? environment.active_runs : "—"}</TableCell>
                </TableRow>
              );
            })}
            {!workers.length && (
              <TableRow className="hover:bg-transparent">
                <TableCell colSpan={8} className="py-6 text-center text-muted-foreground">还没有执行环境。先在主机上启动 Worker，再在这里登记。</TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
      {chosen && <EnvironmentDetail key={chosen.id} worker={chosen} environment={environments.get(chosen.id)} act={act} />}
    </Panel>
  );
}

/** The chosen environment: who it serves, where it is, why it is unreachable, and every administrator action on it. */
function EnvironmentDetail({ worker: w, environment, act }: { worker: Worker; environment: Environment | undefined; act: Act }) {
  const { allTeams: teams, users } = useConsole();
  const [sharing, setSharing] = useState(false);
  const [assign, setAssign] = useState("");
  const label = (space: { team_id?: string; user_id?: string }) => space.team_id
    ? "Team · " + (teams.find((t) => t.id === space.team_id)?.name ?? space.team_id)
    : "个人 · " + (users.find((u) => u.id === space.user_id)?.username ?? space.user_id);
  // Teams take one environment each; a user may join a shared one beside a personal Worker.
  const candidates = [
    ...teams.filter((t) => !t.environment && !t.archived).map((t) => ({ value: "team:" + t.id, text: "Team · " + t.name })),
    ...(w.scope === "shared" ? users.filter((u) => !w.spaces?.some((s) => s.user_id === u.id))
      .map((u) => ({ value: "user:" + u.id, text: "个人 · " + u.username })) : []),
  ];
  const space = (value: string) => {
    const [kind, id] = value.split(":");
    return kind === "team" ? { team_id: id } : { user_id: id };
  };
  const owner = w.scope === "shared" ? `共享环境 · 服务 ${w.spaces?.length ?? 0} 个空间`
    : w.scope === "spare" ? "空闲专属环境 · 待分配给 Team"
    : teams.find((t) => t.id === w.team_id)?.name ?? users.find((u) => u.id === w.owner_id)?.username ?? "已分配空间";
  const facts: [string, string][] = [
    ["归属", owner],
    ...(environment?.owner?.username && w.scope === "shared" ? [["边界所有者", environment.owner.username] as [string, string]] : []),
    ["地址", environment?.url ?? "—"],
    ["隔离", w.capabilities.isolation ?? "—"],
    ...(typeof w.capabilities.checked_at === "number" ? [["最近检查", formatTime(w.capabilities.checked_at, true)] as [string, string]] : []),
  ];

  return (
    <section aria-label={w.name} data-environment={w.id} className="space-y-4 rounded-lg border bg-card px-4 py-4 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-base font-semibold">{w.name}</h3>
        <StatusDot value={status(w, environment)} />
        <span className="flex-1" />
        <Button type="button" size="sm" variant="outline" data-refresh-worker={w.id}
          onClick={act(() => api("/workers/" + w.id + "/capabilities"), "已重新读取 Worker 模型目录。")}>刷新模型</Button>
        {w.scope === "dedicated" && !!w.enabled && (
          <Button type="button" size="sm" variant="outline" data-share-worker={w.id} onClick={() => setSharing(true)}>设为共享环境</Button>
        )}
        {w.scope === "shared" && w.spaces?.length === 1 && !!w.spaces[0].team_id && w.capabilities.isolation === "dedicated" && (
          // A Worker host serving one Team becomes its own again: every member then opens the native entry.
          <Button type="button" size="sm" variant="outline" data-dedicate-worker={w.id}
            onClick={act(() => api("/workers/" + w.id + "/dedicate", "POST", {}), "已改回该 Team 的专属环境。")}>改回专属环境</Button>
        )}
        <WorkerTools worker={w} act={act} />
      </div>
      {environment && (
        <p className={cn("text-xs", environment.health.reachable ? "text-muted-foreground" : "text-destructive")}
          data-environment-health={w.id}>{healthText(environment)}</p>
      )}
      <dl className="grid gap-x-6 gap-y-1.5 text-xs sm:grid-cols-[auto_1fr]">
        {facts.map(([term, value]) => (
          <div key={term} className="contents">
            <dt className="text-muted-foreground">{term}</dt>
            <dd className="break-all">{value}</dd>
          </div>
        ))}
      </dl>
      {w.scope === "shared" && (
        <div className="space-y-1.5">
          <h4 className="text-xs font-semibold text-muted-foreground">服务的空间</h4>
          {w.spaces?.length ? (
            <ul className="flex flex-wrap gap-1.5" aria-label="服务的空间">
              {w.spaces.map((s) => (
                <li key={s.team_id ?? s.user_id} className="flex items-center gap-1 rounded-md bg-muted px-2 py-0.5 text-xs">
                  <span>{label(s)}</span>
                  <button type="button" aria-label={"移出 " + label(s)}
                    className="rounded outline-none hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50"
                    onClick={act(() => api("/workers/" + w.id + "/spaces", "DELETE", s.team_id ? { team_id: s.team_id }
                      : { user_id: s.user_id }), "已移出。")}><X className="size-3" /></button>
                </li>
              ))}
            </ul>
          ) : <p className="text-xs text-muted-foreground">还没有服务任何空间。</p>}
        </div>
      )}
      {!!w.enabled && (w.scope === "shared" || w.scope === "spare") && (
        <div className="flex max-w-xl items-center gap-2">
          <Label htmlFor={"assign-" + w.id} className="shrink-0 text-xs">分配到</Label>
          <NativeSelect id={"assign-" + w.id} value={assign} onChange={(e) => setAssign(e.target.value)}>
            <option value="">{w.scope === "spare" ? "选择 Team" : "选择 Team 或用户"}</option>
            {candidates.map((c) => <option key={c.value} value={c.value}>{c.text}</option>)}
          </NativeSelect>
          <Button type="button" size="sm" variant="outline" disabled={!assign}
            onClick={act(async () => {
              await api("/workers/" + w.id + "/spaces", "POST", space(assign));
              setAssign("");
            }, "已分配。")}>分配</Button>
        </div>
      )}
      {sharing && (
        <div role="alert" className="space-y-2 rounded-md border border-warning/40 bg-warning/5 p-3 text-xs">
          <p>设为共享环境后，管理员可以把 Team 和用户的个人空间分配到它上面。{SHARED_NOTICE}
            共享后只有边界所有者能打开原生 KiroCrew 入口，其他人在会话页的「KiroCrew 会话」中查看。</p>
          <div className="flex gap-2">
            <Button type="button" size="sm" onClick={act(async () => {
              await api("/workers/" + w.id + "/share", "POST", { confirm: true });
              setSharing(false);
            }, "已设为共享环境。")}>确认设为共享环境</Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setSharing(false)}>取消</Button>
          </div>
        </div>
      )}
    </section>
  );
}

type ToolForm = "name" | "connection" | "retire" | null;

/** Administrator tools on one Worker (spec §6.1): rename, a new connection after a host is replaced, enabling, retirement. */
function WorkerTools({ worker, act }: { worker: Worker; act: Act }) {
  const [open, setOpen] = useState<ToolForm>(null);
  const [title, setTitle] = useState(worker.name);
  const [connection, setConnection] = useState({ url: "", token: "" });
  const base = "/workers/" + encodeURIComponent(worker.id);
  const toggle = (tool: ToolForm) => setOpen(open === tool ? null : tool);
  return (
    <>
      <Button type="button" size="sm" variant="outline" onClick={() => toggle("name")}>改名</Button>
      <Button type="button" size="sm" variant="outline" onClick={() => toggle("connection")}>更新连接</Button>
      <Button type="button" size="sm" variant="outline" data-toggle-worker={worker.id}
        onClick={act(() => api(base + "/enabled", "POST", { enabled: !worker.enabled }), worker.enabled ? "已停用。" : "已启用。")}>
        {worker.enabled ? "停用" : "启用"}
      </Button>
      <Button type="button" size="sm" variant="ghost" className="text-destructive" onClick={() => toggle("retire")}>退役</Button>
      {open === "name" && (
        <div className="flex w-full items-end gap-2">
          <div className="max-w-sm flex-1 space-y-1">
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

/** 登记 Worker: a Gateway already started on a host, bound to a person, a Team or kept spare for a Team. */
function RegisterWorker({ onDone, refresh }: { onDone: () => void; refresh: () => Promise<unknown> }) {
  const { allTeams: teams, users, me } = useConsole();
  const [form, setForm] = useState({ name: "", owner: "user:" + me.id, url: "", token: "" });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  async function submit(event: FormEvent) {
    event.preventDefault();
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      const [kind, id] = form.owner.split(":");
      const connection = { url: form.url, token: form.token };
      await api("/workers", "POST", kind === "spare" ? { name: form.name, spare: true, connection }
        : { name: form.name, [kind === "team" ? "team_id" : "owner_id"]: id, connection });
      await refresh();
      toast.success("Worker 已登记。");
      onDone();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <form onSubmit={submit} aria-label="登记 Worker" className="space-y-3 rounded-lg border bg-card px-4 py-4">
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
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      <div className="flex items-center gap-2">
        <Button type="submit" disabled={busy}>{busy ? "处理中…" : "登记"}</Button>
        <Button type="button" variant="ghost" onClick={onDone}>取消</Button>
      </div>
    </form>
  );
}

/** 我的设置 › 执行环境 for someone who is not an administrator: the environments serving them; anyone served may
 * refresh an environment's model catalogue (spec §6.1). */
export function MyEnvironmentsPanel() {
  const { workers, allTeams: teams, users } = useConsole();
  const client = useQueryClient();
  const refresh = (w: Worker) => api("/workers/" + w.id + "/capabilities")
    .then(() => client.invalidateQueries({ queryKey: keys.workers }))
    .then(() => toast.success("已重新读取 Worker 模型目录。")).catch((e: Error) => toast.error(e.message));
  const owner = (w: Worker) => w.scope === "shared" ? "共享环境"
    : teams.find((t) => t.id === w.team_id)?.name ?? users.find((u) => u.id === w.owner_id)?.username ?? "专属环境";
  return (
    <Panel title="执行环境" description="执行环境由管理员登记和分配。新建开发时看不到想用的模型，可以刷新模型。">
      <div className="space-y-2">
        {workers.map((w) => {
          const tools = availableTools(w);
          return (
            <div key={w.id} role="group" aria-label={w.name}
              className="flex flex-wrap items-center gap-2 rounded-lg border bg-card px-3 py-2 text-sm">
              <span className="min-w-0 flex-1">{w.name}
                <small className="ml-2 text-xs text-muted-foreground">
                  {scopeLabel(w)} · {w.enabled ? "可用" : "已停用"} · {owner(w)}
                  {tools?.length ? " · " + tools.join(" · ") : ""}
                  {typeof w.capabilities.checked_at === "number" && ` · 最近检查 ${formatTime(w.capabilities.checked_at, true)}`}
                </small>
              </span>
              <Button type="button" size="sm" variant="outline" data-refresh-worker={w.id} onClick={() => void refresh(w)}>刷新模型</Button>
            </div>
          );
        })}
        {!workers.length && <p className="text-sm text-muted-foreground">还没有为你分配执行环境，请联系平台管理员。</p>}
      </div>
    </Panel>
  );
}
