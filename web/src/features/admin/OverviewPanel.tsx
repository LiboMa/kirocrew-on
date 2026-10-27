import { useQuery } from "@tanstack/react-query";
import { Activity, ChevronRight, Server, TriangleAlert, Users, UsersRound, type LucideIcon } from "lucide-react";
import { api } from "@/api/client";
import { useEnvironments } from "@/api/hooks";
import type { AdminUser } from "@/api/types";
import { Panel } from "@/components/Panel";
import { kiroLogin, status } from "@/features/admin/EnvironmentsPanel";
import { ADMIN_USERS } from "@/features/admin/UsersPanel";
import { useConsole } from "@/features/console/context";
import { cn } from "@/lib/utils";

interface Figure {
  label: string;
  value: string | number;
  hint: string;
  section: string;
  icon: LucideIcon;
  /** The icon tile's tint: muted, one hue per figure so the four tell apart at a glance. */
  tint: string;
}

/** 管理 › 概览: the platform at a glance (青岩) — one flat card per figure, each opening its
 * section; what needs an administrator is listed below. */
export function OverviewPanel() {
  const { workers, allTeams, go } = useConsole();
  const environments = useEnvironments(true);
  const users = useQuery({ queryKey: ADMIN_USERS, queryFn: () => api<AdminUser[]>("/admin/users") });
  const health = new Map((environments.data ?? []).map((e) => [e.id, e]));
  const live = workers.filter((w) => !!w.enabled);
  const reachable = live.filter((w) => status(w, health.get(w.id)) === "ok").length;
  const running = environments.data?.reduce((sum, e) => sum + e.active_runs, 0);
  const people = users.data ?? [];
  const teams = allTeams.filter((t) => !t.archived);
  const archived = allTeams.length - teams.length;
  const issues = live.flatMap((w) => {
    const e = health.get(w.id);
    if (e && !e.health.reachable) return [`${w.name}：不可达（${e.health.error}）`];
    if (kiroLogin(w, e) === "expired") return [`${w.name}：kiro-cli 登录已过期，请边界主人在「我的设置 › 执行环境」里登录 Kiro`];
    return [];
  });
  const figures: Figure[] = [
    { label: "执行环境", value: environments.data ? `${reachable} / ${live.length}` : `— / ${live.length}`, hint: "可达 / 启用",
      section: "environments", icon: Server, tint: "bg-primary/10 text-primary" },
    { label: "进行中的会话", value: running ?? "—", hint: "各执行环境合计", section: "environments",
      icon: Activity, tint: "bg-sky-500/10 text-sky-700 dark:text-sky-300" },
    { label: "用户", value: users.data ? people.filter((u) => u.active).length : "—",
      hint: users.data ? `有效 · 共 ${people.length} 位` : "有效", section: "users",
      icon: Users, tint: "bg-violet-500/10 text-violet-700 dark:text-violet-300" },
    { label: "Team", value: teams.length, hint: archived ? `另有 ${archived} 个已归档` : "在用", section: "teams",
      icon: UsersRound, tint: "bg-amber-500/10 text-amber-700 dark:text-amber-300" },
  ];

  return (
    <Panel title="概览" description="平台此刻的状态，每 30 秒更新；点一项进入对应的管理。">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {figures.map((figure) => (
          // The label comes first in the button's text: its name starts with it (「执行环境 1 / 2 可达 / 启用」).
          <button key={figure.label} type="button" onClick={() => go("admin", figure.section)}
            className="group flex items-center gap-3 rounded-lg border bg-card px-4 py-3.5 text-left outline-none transition-colors hover:border-primary/40 hover:bg-muted/40 focus-visible:ring-[3px] focus-visible:ring-ring/50">
            <span aria-hidden className={cn("grid size-10 shrink-0 place-items-center rounded-md", figure.tint)}>
              <figure.icon className="size-5" />
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-xs text-muted-foreground">{figure.label}</span>
              <strong className="block text-2xl leading-tight font-semibold tabular-nums">{figure.value}</strong>
              <small className="block truncate text-xs text-muted-foreground">{figure.hint}</small>
            </span>
            <ChevronRight aria-hidden className="size-4 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100" />
          </button>
        ))}
      </div>
      {issues.length > 0 && (
        <section aria-label="需要处理" className="overflow-hidden rounded-lg border bg-card">
          <h3 className="flex items-center gap-2 border-b bg-muted/40 px-4 py-2.5 text-sm font-medium">
            <TriangleAlert aria-hidden className="size-4 text-destructive" />需要处理
            <span className="ml-auto text-xs font-normal text-muted-foreground">{issues.length} 项</span>
          </h3>
          {issues.map((issue) => (
            <button key={issue} type="button" onClick={() => go("admin", "environments")}
              className="flex w-full items-center gap-3 border-b px-4 py-2.5 text-left text-sm outline-none transition-colors last:border-0 hover:bg-destructive/5 focus-visible:bg-destructive/5 focus-visible:ring-[3px] focus-visible:ring-ring/50 focus-visible:ring-inset">
              <span aria-hidden className="size-1.5 shrink-0 rounded-full bg-destructive" />
              <span className="min-w-0 flex-1">{issue}</span>
              <ChevronRight aria-hidden className="size-4 shrink-0 text-muted-foreground" />
            </button>
          ))}
        </section>
      )}
    </Panel>
  );
}
