import { useEffect, useRef, useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Archive, Check, Copy, KeyRound, Network, Pencil, Plus, Power, PowerOff, RefreshCw, SquarePlus, Undo2, X } from "lucide-react";
import { api } from "@/api/client";
import { keys, useEnrollments, useEnvironments } from "@/api/hooks";
import type { Enrollment, Environment, IssuedEnrollment, Tool, Worker } from "@/api/types";
import { FormDialog } from "@/components/FormDialog";
import { ListCard, ListEmpty, RowActions, type Action } from "@/components/ListCard";
import { NativeSelect } from "@/components/NativeSelect";
import { Panel } from "@/components/Panel";
import { useRowMotion } from "@/components/rowMotion";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useConsole } from "@/features/console/context";
import { formatTime, TOOLS } from "@/lib/labels";
import { cn } from "@/lib/utils";

/** What a shared execution environment means (D28); shown before an administrator confirms sharing. */
export const SHARED_NOTICE = "环境内所有会话的 Agent 以同一系统账号运行，能互相读取源码、工具登录与记忆；记忆未分隔；只用于同一信任范围。";

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
type Tone = "ok" | "down" | "muted";
const STATUS: Record<Status, { text: string; tone: Tone }> = {
  ok: { text: "可达", tone: "ok" },
  down: { text: "不可达", tone: "down" },
  disabled: { text: "已停用", tone: "muted" },
  unknown: { text: "检查中", tone: "muted" },
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

const TONES: Record<Tone, string> = {
  ok: "bg-success/10 text-success",
  down: "bg-destructive/10 text-destructive",
  muted: "bg-muted text-muted-foreground",
};

/** A small flat status badge (青岩): a dot and a word, tinted by what it means. */
function Pill({ tone, children }: { tone: Tone; children: ReactNode }) {
  return (
    <span className={cn("inline-flex items-center gap-1.5 rounded-sm px-1.5 py-0.5 text-xs font-medium whitespace-nowrap", TONES[tone])}>
      <span className="size-1.5 shrink-0 rounded-full bg-current" aria-hidden />{children}
    </span>
  );
}

const StatusPill = ({ value }: { value: Status }) => <Pill tone={STATUS[value].tone}>{STATUS[value].text}</Pill>;

const LOGIN = { ok: "已登录", expired: "已过期", unknown: "未知" } as const;
const Dash = () => <span className="text-muted-foreground">—</span>;

type Space = { team_id?: string; user_id?: string };
type Open = { kind: "register" }
  | { kind: "edit" | "assign" | "share" | "dedicate" | "disable" | "retire"; worker: Worker }
  | { kind: "remove"; worker: Worker; space: Space }
  | { kind: "revoke-code"; code: Enrollment }
  | null;

/** 管理 › 执行环境 (D28; PRD 2026-10-23, 管理 / 我的设置 in dialogs): every environment with its live status, the chosen
 * one's detail below. The page only lists and shows; registering, editing, assigning, sharing and every dangerous step
 * (停用, 退役, taking a space away) happen in a dialog opened from 「登记 Worker」 or the row. */
export function EnvironmentsPanel() {
  const { workers, allTeams: teams, users } = useConsole();
  const client = useQueryClient();
  const query = useEnvironments(true);
  const environments = new Map((query.data ?? []).map((e) => [e.id, e]));
  const [picked, setPicked] = useState("");
  const [dialog, setDialog] = useState<Open>(null);
  const [search, setSearch] = useState("");
  const motion = useRowMotion();
  // A Worker that registered itself from its host (用接入码, N §3) lights up when it first appears in the list.
  const seen = useRef<Set<string> | null>(null);
  useEffect(() => {
    const ids = new Set(workers.map((w) => w.id));
    if (seen.current) for (const id of ids) if (!seen.current.has(id)) motion.flash(id);
    if (workers.length || seen.current) seen.current = ids;
  }, [workers, motion]);
  const term = search.trim().toLowerCase();
  const shown = term ? workers.filter((w) => w.name.toLowerCase().includes(term)) : workers;
  // An environment that needs attention is shown first; otherwise the first one.
  const chosen = shown.find((w) => w.id === picked) ?? shown.find((w) => troubled(w, environments.get(w.id))) ?? shown[0];
  const refresh = () => Promise.all([keys.workers, keys.teams, keys.environments].map((queryKey) => client.invalidateQueries({ queryKey })));
  const spaceName = (space: Space) => space.team_id
    ? "Team · " + (teams.find((t) => t.id === space.team_id)?.name ?? space.team_id)
    : "个人 · " + (users.find((u) => u.id === space.user_id)?.username ?? space.user_id);
  /** The one Team a shared Worker host serves, which 改回专属环境 makes it the own environment of. */
  const servedTeam = (w: Worker) => teams.find((t) => t.id === w.spaces?.[0]?.team_id)?.name ?? w.spaces?.[0]?.team_id;
  /** What a dialog's change ends with: the list read again, the dialog closed, the row lit up and a toast. */
  const changed = async (id: string, done: string) => {
    await refresh();
    setDialog(null);
    motion.flash(id);
    toast.success(done);
  };
  /** A step that needs no dialog (刷新模型, 启用): it runs at once and says how it went. */
  const direct = (w: Worker, action: () => Promise<unknown>, done: string) => () => void action().then(refresh)
    .then(() => { motion.flash(w.id); toast.success(done); }).catch((e: Error) => toast.error(e.message));

  const rowActions = (w: Worker) => {
    const open = (kind: "edit" | "assign" | "share" | "dedicate" | "disable" | "retire") => () => setDialog({ kind, worker: w });
    const enabled = !!w.enabled;
    // A Worker host serving one Team can become its own again (not the platform's trusted-local one).
    const dedicable = w.scope === "shared" && w.spaces?.length === 1 && !!w.spaces[0].team_id && w.capabilities.isolation === "dedicated";
    const actions: Action[] = [
      { icon: Pencil, label: "编辑", name: "编辑：" + w.name, onSelect: open("edit") },
      ...(enabled && (w.scope === "shared" || w.scope === "spare")
        ? [{ icon: SquarePlus, label: "分配到空间", name: "分配到空间：" + w.name, onSelect: open("assign") }] : []),
    ];
    const more: Action[] = [
      { icon: RefreshCw, label: "刷新模型", name: "刷新模型：" + w.name, direct: true,
        onSelect: direct(w, () => api("/workers/" + w.id + "/capabilities", "POST", {}), `已重新读取 ${w.name} 的模型目录`) },
      ...(w.scope === "dedicated" && enabled ? [{ icon: Network, label: "设为共享环境", name: "设为共享环境：" + w.name, onSelect: open("share") }] : []),
      ...(dedicable ? [{ icon: Undo2, label: "改回专属环境", name: "改回专属环境：" + w.name, onSelect: open("dedicate") }] : []),
      // 停用 can be undone (启用), so only 退役 is set apart as dangerous; both still ask first.
      enabled ? { icon: PowerOff, label: "停用", name: "停用：" + w.name, onSelect: open("disable") }
        : { icon: Power, label: "启用", name: "启用：" + w.name, direct: true,
          onSelect: direct(w, () => api("/workers/" + w.id + "/enabled", "POST", { enabled: true }), "已启用 " + w.name) },
      { icon: Archive, label: "退役", name: "退役：" + w.name, tone: "danger", onSelect: open("retire") },
    ];
    return <RowActions menu={"执行环境操作：" + w.name} actions={actions} more={more} />;
  };

  return (
    <Panel title="执行环境"
      description="每个 Worker 对应一个 KiroCrew Gateway。状态每 30 秒自动读取一次，列表上方的刷新按钮可立即刷新状态；isolation 字段是部署声明，不能替代实际操作系统隔离。"
      actions={<Button type="button" onClick={() => setDialog({ kind: "register" })}><Plus />登记 Worker</Button>}>
      {query.error && <p className="text-sm text-destructive">状态读取失败：{query.error.message}</p>}
      <ListCard label="执行环境列表" search={search} onSearch={setSearch} searchLabel="搜索执行环境名称"
        onRefresh={() => void refresh()} refreshing={query.isFetching} summary={`共 ${workers.length} 个`}>
        {!workers.length ? <ListEmpty>还没有执行环境。先在主机上启动 Worker，再点「登记 Worker」。</ListEmpty>
          : !shown.length ? <ListEmpty>没有名称含「{search.trim()}」的执行环境。</ListEmpty> : (
          <Table>
            <TableHeader>
              <TableRow className="bg-muted/40 hover:bg-muted/40">
                <TableHead className="pl-4">状态</TableHead><TableHead>名称</TableHead><TableHead>类型</TableHead>
                <TableHead>版本</TableHead><TableHead>Kiro 登录</TableHead><TableHead>可用工具</TableHead>
                <TableHead className="text-right">服务空间</TableHead><TableHead className="text-right">进行中会话</TableHead>
                <TableHead className="pr-4 text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {shown.map((w) => {
                const environment = environments.get(w.id);
                const login = kiroLogin(w, environment);
                const tools = availableTools(w);
                const selected = w.id === chosen?.id;
                const motionProps = motion.props(w.id);
                return (
                  <TableRow key={w.id} data-environment-row={w.id} data-state={selected ? "selected" : undefined}
                    data-fresh={motionProps["data-fresh"]} className={cn("cursor-pointer", motionProps.className)}
                    onClick={() => setPicked(w.id)}>
                    <TableCell className="pl-4"><StatusPill value={status(w, environment)} /></TableCell>
                    <TableCell>
                      <button type="button" aria-pressed={selected} onClick={(e) => { e.stopPropagation(); setPicked(w.id); }}
                        className="rounded-sm font-medium outline-none hover:underline focus-visible:ring-[3px] focus-visible:ring-ring/50">
                        {w.name}
                      </button>
                    </TableCell>
                    <TableCell className="text-xs">{scopeLabel(w)}</TableCell>
                    <TableCell className="text-xs tabular-nums">
                      {environment?.health.reachable && environment.health.version ? "v" + environment.health.version : <Dash />}
                    </TableCell>
                    <TableCell className={cn("text-xs", login === "expired" && "font-medium text-destructive")}>
                      {login ? LOGIN[login] : <Dash />}
                    </TableCell>
                    <TableCell className="min-w-40 text-xs whitespace-normal">
                      {tools?.length ? tools.join(" · ") : <span className="text-muted-foreground">{tools ? "没有可用工具" : "—"}</span>}
                    </TableCell>
                    <TableCell className="text-right tabular-nums">{w.scope === "shared" ? w.spaces?.length ?? 0 : <Dash />}</TableCell>
                    <TableCell className="text-right tabular-nums">{environment ? environment.active_runs : "—"}</TableCell>
                    <TableCell className="pr-4 text-right">{rowActions(w)}</TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </ListCard>
      <EnrollmentCodes spaceName={spaceName} onRevoke={(code) => setDialog({ kind: "revoke-code", code })} />
      {chosen && (
        <EnvironmentDetail key={chosen.id} worker={chosen} environment={environments.get(chosen.id)} spaceName={spaceName}
          onRemove={(space) => setDialog({ kind: "remove", worker: chosen, space })} />
      )}
      {dialog?.kind === "revoke-code" && (
        <FormDialog title="撤销接入码" destructive submitLabel="撤销" onClose={() => setDialog(null)}
          description={<>撤销后，接入码「{dialog.code.name}」不能再用来登记 Worker。已经登记的执行环境不受影响。</>}
          onSubmit={async () => {
            await api("/workers/enrollments/" + dialog.code.id, "DELETE", {});
            await client.invalidateQueries({ queryKey: keys.enrollments });
            setDialog(null);
            toast.success("已撤销接入码 " + dialog.code.name);
          }} />
      )}
      {dialog?.kind === "register" && (
        <RegisterDialog refresh={refresh} onClose={(made) => {
          setDialog(null);
          if (made) {
            motion.flash(made.id);
            toast.success("已登记 Worker " + made.name);
          }
        }} />
      )}
      {dialog?.kind === "edit" && (
        <EditDialog worker={dialog.worker} url={environments.get(dialog.worker.id)?.url ?? ""} refresh={refresh} onClose={(saved) => {
          setDialog(null);
          if (saved) {
            motion.flash(dialog.worker.id);
            toast.success("已保存 Worker " + saved);
          }
        }} />
      )}
      {dialog?.kind === "assign" && (
        <AssignDialog worker={dialog.worker} onClose={() => setDialog(null)}
          onAssign={async (space, text) => {
            await api("/workers/" + dialog.worker.id + "/spaces", "POST", space);
            await changed(dialog.worker.id, `已把 ${dialog.worker.name} 分配到 ${text}`);
          }} />
      )}
      {dialog?.kind === "remove" && (
        <FormDialog title="移出空间" destructive submitLabel="移出" onClose={() => setDialog(null)}
          description={<>{spaceName(dialog.space)} 的新会话不再派到「{dialog.worker.name}」；正在执行的任务会做完，之后的会话等待分配到其他执行环境。</>}
          onSubmit={async () => {
            const { worker: w, space } = dialog;
            await api("/workers/" + w.id + "/spaces", "DELETE", space.team_id ? { team_id: space.team_id } : { user_id: space.user_id });
            await changed(w.id, `已把 ${spaceName(space)} 移出 ${w.name}`);
          }} />
      )}
      {dialog?.kind === "share" && (
        <FormDialog title="设为共享环境" submitLabel="设为共享环境" onClose={() => setDialog(null)}
          description={<>设为共享环境后，管理员可以把 Team 和用户的个人空间分配到「{dialog.worker.name}」上。{SHARED_NOTICE}
            共享后只有边界所有者能打开原生 KiroCrew 入口，其他人在会话页的「KiroCrew 会话」中查看。</>}
          onSubmit={async () => {
            await api("/workers/" + dialog.worker.id + "/share", "POST", { confirm: true });
            await changed(dialog.worker.id, `已把 ${dialog.worker.name} 设为共享环境`);
          }} />
      )}
      {dialog?.kind === "dedicate" && (
        <FormDialog title="改回专属环境" submitLabel="改回专属环境" onClose={() => setDialog(null)}
          description={<>改回后，「{dialog.worker.name}」成为 Team「{servedTeam(dialog.worker)}」的专属环境，不再是共享环境：
            Team 的每位成员都能打开它的原生 KiroCrew 入口，与个人空间一样。</>}
          onSubmit={async () => {
            await api("/workers/" + dialog.worker.id + "/dedicate", "POST", {});
            await changed(dialog.worker.id, `已把 ${dialog.worker.name} 改回 ${servedTeam(dialog.worker)} 的专属环境`);
          }} />
      )}
      {dialog?.kind === "disable" && (
        <FormDialog title="停用执行环境" destructive submitLabel="停用" onClose={() => setDialog(null)}
          description={<>停用后，新的会话不会再派到「{dialog.worker.name}」；它上面进行中的会话会暂停，显示「执行环境已停用」，重新启用后自动继续。</>}
          onSubmit={async () => {
            await api("/workers/" + dialog.worker.id + "/enabled", "POST", { enabled: false });
            await changed(dialog.worker.id, "已停用 " + dialog.worker.name);
          }} />
      )}
      {dialog?.kind === "retire" && (
        <FormDialog title="退役执行环境" destructive submitLabel="退役" onClose={() => setDialog(null)}
          description={<>退役后不再出现在列表与选择器中，也不能重新启用；历史会话保留对「{dialog.worker.name}」的引用，它服务的个人空间会失去这个环境。
            仍是某个 Team 的执行环境，或还有进行中的会话时，平台会拒绝退役。这一步不能撤回。</>}
          onSubmit={async () => {
            const { id, name } = dialog.worker;
            await api("/workers/" + encodeURIComponent(id) + "/retire", "POST", {});
            setDialog(null);
            await motion.leave(id, refresh);
            toast.success("已退役 " + name);
          }} />
      )}
    </Panel>
  );
}

/** The chosen environment, read-only: why it is (un)reachable, who it serves and where it is. Taking a space away from a
 * shared one starts here (its 「×」), and asks first. */
function EnvironmentDetail({ worker: w, environment, spaceName, onRemove }: {
  worker: Worker; environment: Environment | undefined; spaceName: (space: Space) => string; onRemove: (space: Space) => void;
}) {
  const { allTeams: teams, users } = useConsole();
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
    <section aria-label={w.name} data-environment={w.id} className="overflow-hidden rounded-lg border bg-card text-sm">
      <header className="flex flex-wrap items-center gap-2 border-b bg-muted/40 px-4 py-2.5">
        <h3 className="font-medium">{w.name}</h3>
        <StatusPill value={status(w, environment)} />
        <span className="text-xs text-muted-foreground">{scopeLabel(w)}</span>
      </header>
      <div className="space-y-3 px-4 py-3">
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
            <h4 className="text-xs font-medium text-muted-foreground">服务的空间</h4>
            {w.spaces?.length ? (
              <ul className="flex flex-wrap gap-1.5" aria-label="服务的空间">
                {w.spaces.map((s) => (
                  <li key={s.team_id ?? s.user_id} className="flex items-center gap-1 rounded-sm border bg-muted/40 py-0.5 pr-1 pl-2 text-xs">
                    <span>{spaceName(s)}</span>
                    <button type="button" aria-label={"移出 " + spaceName(s)} title="移出"
                      className="rounded-sm text-muted-foreground outline-none hover:bg-destructive/10 hover:text-destructive focus-visible:ring-[3px] focus-visible:ring-ring/50"
                      onClick={() => onRemove(s)}><X className="size-3" /></button>
                  </li>
                ))}
              </ul>
            ) : <p className="text-xs text-muted-foreground">还没有服务任何空间。</p>}
          </div>
        )}
      </div>
    </section>
  );
}

/** A small titled group of a dialog's fields (PRD 2026-10-23 §3.1: many fields are split into short sections). */
function Fields({ title, note, children }: { title: string; note?: ReactNode; children: ReactNode }) {
  return (
    <fieldset className="min-w-0 space-y-3">
      <legend className="text-xs font-medium text-muted-foreground">{title}</legend>
      {note && <p className="text-xs text-muted-foreground">{note}</p>}
      {children}
    </fieldset>
  );
}

/** How long a new enrollment code lasts (N §4: 30 minutes by default, a day at most). */
const CODE_LIFETIMES: [number, string][] = [[1800, "30 分钟"], [7200, "2 小时"], [86400, "24 小时"]];

/** 登记 Worker: a Gateway already started on a host, bound to a person, a Team or kept spare for a Team — either with
 * its address and token typed in here, or (用接入码, N §3) with a one-time code the Worker host uses to register itself,
 * so its token never passes through a person. The code is shown once, on the dialog's second screen. */
function RegisterDialog({ refresh, onClose }: { refresh: () => Promise<unknown>; onClose: (made?: { id: string; name: string }) => void }) {
  const { allTeams: teams, users, me } = useConsole();
  const client = useQueryClient();
  const [form, setForm] = useState({ name: "", owner: "user:" + me.id, url: "", token: "", lifetime: "1800" });
  const [method, setMethod] = useState<"connection" | "code">("connection");
  const [issued, setIssued] = useState<IssuedEnrollment | null>(null);
  const [opener] = useState(() => document.activeElement as HTMLElement | null);   // through both screens
  if (issued) return <IssuedCodeDialog issued={issued} returnTo={opener} onClose={() => onClose()} />;
  return (
    <FormDialog title="登记 Worker" submitLabel={method === "code" ? "签发接入码" : "登记"} onClose={() => onClose()}
      description="把已在主机上启动的 KiroCrew Gateway 登记为执行环境。Worker 连接凭据由管理员填写，普通用户不会获得；Gateway 与 Coding 工具的认证保留在 Worker 内。"
      onSubmit={async () => {
        const [kind, id] = form.owner.split(":");
        const name = form.name.trim();
        const binding = kind === "spare" ? { spare: true } : { [kind === "team" ? "team_id" : "owner_id"]: id };
        if (method === "code") {
          setIssued(await api<IssuedEnrollment>("/workers/enrollments", "POST", { name, ...binding, expires_in: Number(form.lifetime) }));
          await client.invalidateQueries({ queryKey: keys.enrollments });
          return;
        }
        const made = await api<{ id: string }>("/workers", "POST", { name, ...binding, connection: { url: form.url.trim(), token: form.token } });
        await refresh();
        onClose({ id: made.id, name });
      }}>
      <div className="grid gap-3 sm:grid-cols-2" role="radiogroup" aria-label="登记方式">
        {([["connection", "填写连接", "填 Worker 的地址与访问 Token"],
          ["code", "用接入码", "签发一次性接入码，Worker 主机凭它自己登记，Token 不经人手"]] as const).map(([value, title, detail]) => (
          <label key={value}
            className={cn("flex cursor-pointer flex-col gap-1 rounded-md border p-3 has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring",
              method === value ? "border-primary bg-primary/5" : "hover:bg-accent")}>
            <input type="radio" name="register-method" value={value} checked={method === value} className="sr-only"
              aria-label={title} onChange={() => setMethod(value)} />
            <strong className="text-sm">{title}</strong>
            <small className="text-xs text-muted-foreground">{detail}</small>
          </label>
        ))}
      </div>
      <Fields title="环境">
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="worker-name">名称</Label>
            <Input id="worker-name" autoFocus required maxLength={80} value={form.name}
              onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="team-a-worker" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="worker-owner">绑定空间</Label>
            <NativeSelect id="worker-owner" value={form.owner} onChange={(e) => setForm({ ...form, owner: e.target.value })}>
              <option value="spare:">空闲专属环境（之后分配给 Team）</option>
              {users.map((u) => <option key={u.id} value={"user:" + u.id}>个人 · {u.username}</option>)}
              {teams.filter((t) => !t.archived).map((t) => <option key={t.id} value={"team:" + t.id}>Team · {t.name}</option>)}
            </NativeSelect>
          </div>
        </div>
      </Fields>
      {method === "code" ? (
        <Fields title="接入码" note="接入码只用一次，只显示一次；Worker 用它登记时，平台照样核验它的执行边界与隔离声明。">
          <div className="space-y-1.5 sm:w-1/2">
            <Label htmlFor="worker-code-lifetime">有效期</Label>
            <NativeSelect id="worker-code-lifetime" value={form.lifetime} onChange={(e) => setForm({ ...form, lifetime: e.target.value })}>
              {CODE_LIFETIMES.map(([value, label]) => <option key={value} value={String(value)}>{label}</option>)}
            </NativeSelect>
          </div>
        </Fields>
      ) : (
      <Fields title="连接" note="主机上 Worker 的地址与它的访问 Token；登记时平台会核验它的执行边界与隔离声明。">
        <div className="grid gap-3 sm:grid-cols-2">
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
      </Fields>
      )}
    </FormDialog>
  );
}

/** A new enrollment code and the command to run on the Worker host, shown this once (N §3). */
function IssuedCodeDialog({ issued, returnTo, onClose }: { issued: IssuedEnrollment; returnTo: HTMLElement | null; onClose: () => void }) {
  const [copied, setCopied] = useState("");
  const copy = (what: string, value: string) => () => navigator.clipboard?.writeText(value).then(() => { setCopied(what); toast.success("已复制"); });
  const line = (what: string, value: string, label: string) => (
    <div className="space-y-1.5">
      <p className="text-xs font-medium text-muted-foreground">{label}</p>
      <div className="flex items-center gap-2 rounded-md border bg-muted/50 p-3">
        <code className="secret min-w-0 flex-1 font-mono text-xs break-all">{value}</code>
        <Button type="button" size="sm" variant={copied === what ? "secondary" : "outline"} aria-label={"复制" + label} onClick={copy(what, value)}>
          {copied === what ? <Check /> : <Copy />}{copied === what ? "已复制" : "复制"}
        </Button>
      </div>
    </div>
  );
  return (
    <FormDialog title="接入码" closeLabel="我已保存" onClose={onClose} returnTo={returnTo}
      description={<>接入码只显示这一次，用一次即失效，{formatTime(issued.expires)} 前有效。Worker「{issued.name}」登记后会出现在列表里。</>}>
      {line("code", issued.code, "接入码")}
      {line("command", issued.command, "在 Worker 主机上以 root 运行")}
      <p className="text-xs text-muted-foreground">命令运行后粘贴接入码并回车；接入码不写在命令里，不会留在 shell 历史中。</p>
    </FormDialog>
  );
}

/** The unused enrollment codes (N §5), each revocable; the codes themselves are never shown again. */
function EnrollmentCodes({ spaceName, onRevoke }: { spaceName: (space: Space) => string; onRevoke: (code: Enrollment) => void }) {
  const query = useEnrollments(true);
  const unused = (query.data ?? []).filter((e) => e.state === "unused");
  if (!unused.length) return null;
  const where = (e: Enrollment) => e.binding_kind === "spare" ? "空闲专属环境"
    : spaceName(e.binding_kind === "team" ? { team_id: e.binding_id ?? "" } : { user_id: e.binding_id ?? "" });
  return (
    <section aria-label="接入码" className="space-y-2 rounded-md border p-4">
      <h3 className="text-sm font-medium">接入码<span className="ml-2 text-xs font-normal text-muted-foreground">未使用 {unused.length} 个</span></h3>
      <ul className="divide-y">
        {unused.map((e) => (
          <li key={e.id} className="flex items-center gap-3 py-2 text-sm">
            <span className="font-medium">{e.name}</span>
            <span className="text-xs text-muted-foreground">{where(e)}</span>
            <span className="ml-auto text-xs text-muted-foreground tabular-nums">{formatTime(e.expires)} 前有效</span>
            <Button type="button" size="sm" variant="outline" aria-label={"撤销接入码：" + e.name} onClick={() => onRevoke(e)}>
              <X />撤销
            </Button>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** 编辑 (spec §6.1): a Worker's name, and — after its host is replaced or its token rotated — its connection. The
 * connection is kept unless a new token is given; a new address alone is refused, as the server needs both. The
 * address shown follows the environments' answer until it is edited: the dialog may open before every Worker has been
 * probed (seconds when one is unreachable), and an address the person never touched is not a new one. */
function EditDialog({ worker, url: current, refresh, onClose }: {
  worker: Worker; url: string; refresh: () => Promise<unknown>; onClose: (saved?: string) => void;
}) {
  const [title, setTitle] = useState(worker.name);
  const [edited, setUrl] = useState<string | null>(null);
  const url = edited ?? current;
  const [secret, setSecret] = useState("");
  const base = "/workers/" + encodeURIComponent(worker.id);
  return (
    <FormDialog title="编辑 Worker" submitLabel="保存" onClose={() => onClose()}
      description={<>修改「{worker.name}」的名称；主机替换或 Token 轮换后，在这里更换它的连接。</>}
      onSubmit={async () => {
        const name = title.trim();
        const address = url.trim();
        const reconnect = !!secret || address !== current;
        if (!name) throw new Error("请填写 Worker 名称。");
        if (reconnect && (!address || !secret)) throw new Error("更换连接需要同时填写 Worker URL 与 Worker Token。");
        if (!reconnect && name === worker.name) return onClose();   // nothing changed: nothing to say
        // The connection first: it is re-validated like a registration, the likelier of the two to be refused.
        if (reconnect) await api(base + "/connection", "POST", { connection: { url: address, token: secret } });
        if (name !== worker.name) await api(base, "PATCH", { name });
        await refresh();
        onClose(name);
      }}>
      <div className="space-y-1.5">
        <Label htmlFor="worker-title">Worker 名称</Label>
        <Input id="worker-title" autoFocus required maxLength={80} value={title} onChange={(e) => setTitle(e.target.value)} />
      </div>
      <Fields title="连接" note="用于主机替换或 Token 轮换；新地址按登记时的规则重新核验。执行边界变化（重新部署的主机）后，该环境才可分配给其他 Team。">
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="worker-url">Worker URL</Label>
            <Input id="worker-url" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://10.0.0.9:8943" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="worker-token">Worker Token</Label>
            <Input id="worker-token" type="password" autoComplete="off" minLength={32} value={secret}
              onChange={(e) => setSecret(e.target.value)} placeholder="不更换连接时留空" />
          </div>
        </div>
      </Fields>
    </FormDialog>
  );
}

/** 分配到空间: a spare environment goes to one Team without an environment (it becomes that Team's own); a shared one
 * takes Teams without one and users it does not serve yet. */
function AssignDialog({ worker: w, onAssign, onClose }: {
  worker: Worker; onAssign: (space: Space, text: string) => Promise<void>; onClose: () => void;
}) {
  const { allTeams: teams, users } = useConsole();
  const [value, setValue] = useState("");
  const candidates = [
    ...teams.filter((t) => !t.environment && !t.archived).map((t) => ({ value: "team:" + t.id, text: "Team · " + t.name })),
    ...(w.scope === "shared" ? users.filter((u) => !w.spaces?.some((s) => s.user_id === u.id))
      .map((u) => ({ value: "user:" + u.id, text: "个人 · " + u.username })) : []),
  ];
  return (
    <FormDialog title="分配到空间" submitLabel="分配" onClose={onClose}
      description={w.scope === "spare"
        ? <>「{w.name}」是空闲专属环境：只能分配给一个还没有执行环境的 Team，分配后成为该 Team 的专属环境。</>
        : <>「{w.name}」是共享环境，可以同时服务多个 Team 与个人空间；已在服务的空间不再列出。</>}
      onSubmit={async () => {
        const chosen = candidates.find((c) => c.value === value);
        if (!chosen) throw new Error("先选择一个空间。");
        const [kind, id] = value.split(":");
        await onAssign(kind === "team" ? { team_id: id } : { user_id: id }, chosen.text);
      }}>
      <div className="space-y-1.5">
        <Label htmlFor="assign-space">空间</Label>
        <NativeSelect id="assign-space" autoFocus value={value} onChange={(e) => setValue(e.target.value)}>
          <option value="">{w.scope === "spare" ? "选择 Team" : "选择 Team 或用户"}</option>
          {candidates.map((c) => <option key={c.value} value={c.value}>{c.text}</option>)}
        </NativeSelect>
        {!candidates.length && <p className="text-xs text-muted-foreground">没有可分配的空间：每个 Team 都已有执行环境{w.scope === "shared" ? "，用户也都已在其中" : ""}。</p>}
      </div>
      {w.scope === "shared" && <p className="text-xs text-muted-foreground">共享环境：{SHARED_NOTICE}</p>}
    </FormDialog>
  );
}

/** 我的设置 › 执行环境 for someone who is not an administrator: the environments serving them, read-only; anyone served
 * may refresh an environment's model catalogue (spec §6.1). */
/** Kiro signed in from the console with a device code (MVP 0.2.0, §6.4): the environment's owner opens the address, enters
 * the code, and the dialog sees the login finish. The code is shown here only and nowhere kept. */
function KiroLoginDialog({ worker, onClose }: { worker: Worker; onClose: (done?: boolean) => void }) {
  type Login = { state: "idle" | "starting" | "waiting" | "done" | "failed"; url?: string; code?: string; expires?: number };
  const [login, setLogin] = useState<Login | null>(null);
  const [error, setError] = useState("");
  const [copied, setCopied] = useState(false);
  const path = "/workers/" + worker.id + "/kiro-login";
  useEffect(() => {
    let live = true;
    let timer: number | undefined;
    const settle = (next: Login) => {
      if (!live) return;
      if (next.state === "done") return onClose(true);
      setLogin(next);
      if (next.state === "waiting" || next.state === "starting") timer = window.setTimeout(poll, 3000);
    };
    const poll = () => void api<Login>(path).then(settle).catch((e: Error) => live && setError(e.message));
    void api<Login>(path, "POST", {}).then(settle).catch((e: Error) => live && setError(e.message));
    return () => { live = false; window.clearTimeout(timer); };
  }, []);   // once per dialog: a second login request would only report the same code
  return (
    <FormDialog title="登录 Kiro" onClose={() => onClose()}
      description={<>为执行环境「{worker.name}」登录 kiro-cli。在浏览器里打开下面的地址、输入设备码；完成后这里会自动确认。</>}>
      {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
      {!error && !login && <p className="text-sm text-muted-foreground">正在取得设备码…</p>}
      {login?.state === "waiting" && login.url && login.code && (
        <div className="space-y-3">
          <a href={login.url} target="_blank" rel="noopener noreferrer" className="block text-sm break-all text-primary underline">{login.url}</a>
          <div className="flex items-center gap-2 rounded-md border bg-muted/50 p-3">
            <code className="min-w-0 flex-1 font-mono text-lg tracking-widest">{login.code}</code>
            <Button type="button" size="sm" variant={copied ? "secondary" : "outline"} aria-label="复制设备码"
              onClick={() => navigator.clipboard?.writeText(login.code ?? "").then(() => setCopied(true))}>
              {copied ? <Check /> : <Copy />}{copied ? "已复制" : "复制"}
            </Button>
          </div>
          {login.expires && <p className="text-xs text-muted-foreground">设备码在 {formatTime(login.expires)} 前有效。</p>}
        </div>
      )}
      {login?.state === "failed" && <p role="alert" className="text-sm text-destructive">登录没有完成（设备码过期或被拒绝），关闭后可以再试一次。</p>}
    </FormDialog>
  );
}

export function MyEnvironmentsPanel() {
  const { workers, allTeams: teams, users, me } = useConsole();
  const [signing, setSigning] = useState<Worker | null>(null);
  const client = useQueryClient();
  const motion = useRowMotion();
  const refresh = (w: Worker) => api("/workers/" + w.id + "/capabilities", "POST", {})
    .then(() => client.invalidateQueries({ queryKey: keys.workers }))
    .then(() => { motion.flash(w.id); toast.success(`已重新读取 ${w.name} 的模型目录`); }).catch((e: Error) => toast.error(e.message));
  const owner = (w: Worker) => w.scope === "shared" ? "共享环境"
    : teams.find((t) => t.id === w.team_id)?.name ?? users.find((u) => u.id === w.owner_id)?.username ?? "专属环境";
  return (
    <Panel title="执行环境" description="执行环境由管理员登记和分配。新建开发时看不到想用的模型，可以刷新模型。">
      <ListCard label="执行环境列表" summary={`共 ${workers.length} 个`}>
        {!workers.length ? <ListEmpty>还没有为你分配执行环境，请联系平台管理员。</ListEmpty> : (
          <Table>
            <TableHeader>
              <TableRow className="bg-muted/40 hover:bg-muted/40">
                <TableHead className="pl-4">名称</TableHead><TableHead>类型</TableHead><TableHead>状态</TableHead>
                <TableHead>归属</TableHead><TableHead>可用工具</TableHead><TableHead>最近检查</TableHead>
                <TableHead className="pr-4 text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {workers.map((w) => {
                const tools = availableTools(w);
                const motionProps = motion.props(w.id);
                return (
                  <TableRow key={w.id} data-environment-row={w.id} data-fresh={motionProps["data-fresh"]} className={motionProps.className}>
                    <TableCell className="pl-4 font-medium">{w.name}</TableCell>
                    <TableCell className="text-xs">{scopeLabel(w)}</TableCell>
                    <TableCell>{w.enabled ? <Pill tone="ok">可用</Pill> : <Pill tone="muted">已停用</Pill>}</TableCell>
                    <TableCell className="text-xs">{owner(w)}</TableCell>
                    <TableCell className="min-w-40 text-xs whitespace-normal">
                      {tools?.length ? tools.join(" · ") : <span className="text-muted-foreground">{tools ? "没有可用工具" : "—"}</span>}
                    </TableCell>
                    <TableCell className="text-xs text-muted-foreground tabular-nums">
                      {typeof w.capabilities.checked_at === "number" ? formatTime(w.capabilities.checked_at, true) : "—"}
                    </TableCell>
                    <TableCell className="pr-4 text-right">
                      <RowActions menu={"执行环境操作：" + w.name} actions={[
                        { icon: RefreshCw, label: "刷新模型", name: "刷新模型：" + w.name, onSelect: () => void refresh(w) },
                        // only its owner: the code signs the environment in as whoever enters it (§6.4)
                        ...(w.owner_id === me.id ? [{ icon: KeyRound, label: "登录 Kiro", name: "登录 Kiro：" + w.name,
                          onSelect: () => setSigning(w) }] : []),
                      ]} />
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </ListCard>
      {signing && <KiroLoginDialog worker={signing} onClose={(done) => {
        setSigning(null);
        if (done) {
          void client.invalidateQueries({ queryKey: keys.workers });
          motion.flash(signing.id);
          toast.success("已登录 Kiro：" + signing.name);
        }
      }} />}
    </Panel>
  );
}
