import { useQuery } from "@tanstack/react-query";
import { ChevronRight } from "lucide-react";
import { api } from "@/api/client";
import { useEnvironments } from "@/api/hooks";
import type { AdminUser } from "@/api/types";
import { Panel } from "@/components/Panel";
import { kiroLogin, status } from "@/features/admin/EnvironmentsPanel";
import { ADMIN_USERS } from "@/features/admin/UsersPanel";
import { useConsole } from "@/features/console/context";

/** 管理 › 概览: the platform at a glance; each figure opens its section, and what needs an administrator is listed. */
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
    if (kiroLogin(w, e) === "expired") return [`${w.name}：kiro-cli 登录已过期，需要在主机上重新登录（deploy.sh kiro-login）`];
    return [];
  });
  const figures = [
    { label: "执行环境", value: environments.data ? `${reachable} / ${live.length}` : `— / ${live.length}`, hint: "可达 / 启用", section: "environments" },
    { label: "进行中的会话", value: running ?? "—", hint: "各执行环境合计", section: "environments" },
    { label: "用户", value: users.data ? people.filter((u) => u.active).length : "—",
      hint: users.data ? `有效 · 共 ${people.length} 位` : "有效", section: "users" },
    { label: "Team", value: teams.length, hint: archived ? `另有 ${archived} 个已归档` : "在用", section: "teams" },
  ];

  return (
    <Panel title="概览" description="平台此刻的状态，每 30 秒更新；点一项进入对应的管理。">
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {figures.map((figure) => (
          <button key={figure.label} type="button" onClick={() => go("admin", figure.section)}
            className="group rounded-xl border bg-card px-5 py-4 text-left outline-none transition-colors hover:border-primary/40 hover:bg-accent focus-visible:ring-[3px] focus-visible:ring-ring/50">
            <span className="flex items-center justify-between text-xs text-muted-foreground">
              {figure.label}<ChevronRight className="size-3.5 opacity-0 transition-opacity group-hover:opacity-100" aria-hidden />
            </span>
            <strong className="mt-1 block text-2xl tabular-nums">{figure.value}</strong>
            <small className="text-xs text-muted-foreground">{figure.hint}</small>
          </button>
        ))}
      </div>
      {issues.length > 0 && (
        <section aria-label="需要处理" className="space-y-2">
          <h3 className="text-sm font-semibold">需要处理</h3>
          {issues.map((issue) => (
            <button key={issue} type="button" onClick={() => go("admin", "environments")}
              className="flex w-full items-center justify-between rounded-lg border border-destructive/25 bg-destructive/5 px-4 py-2.5 text-left text-sm outline-none hover:bg-destructive/10 focus-visible:ring-[3px] focus-visible:ring-ring/50">
              <span>{issue}</span><ChevronRight className="size-4 shrink-0 text-muted-foreground" aria-hidden />
            </button>
          ))}
        </section>
      )}
    </Panel>
  );
}
