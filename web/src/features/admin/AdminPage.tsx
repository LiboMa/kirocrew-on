import { Gauge, KeyRound, Server, Users, UsersRound } from "lucide-react";
import { EnvironmentsPanel } from "@/features/admin/EnvironmentsPanel";
import { UsersPanel } from "@/features/admin/UsersPanel";
import { useConsole } from "@/features/console/context";
import { PageShell, type PageSection } from "@/features/console/PageShell";
import { CredentialsPanel } from "@/features/settings/CredentialsPanel";
import { QuotasPanel } from "@/features/settings/QuotasPanel";
import { TeamPanel } from "@/features/settings/TeamPanel";
import { TokensPanel } from "@/features/settings/TokensPanel";

export const ADMIN_SECTIONS: PageSection[] = [
  { id: "environments", label: "执行环境", icon: Server },
  { id: "users", label: "用户", icon: Users },
  { id: "teams", label: "Team", icon: UsersRound },
  { id: "access", label: "接入与凭证", icon: KeyRound },
  { id: "quotas", label: "并发上限", icon: Gauge },
];

/** 管理 (elevated administrators): every section of the platform's administration on one page. */
export function AdminPage() {
  const { section, go } = useConsole();
  const current = ADMIN_SECTIONS.some((s) => s.id === section) ? section : ADMIN_SECTIONS[0].id;
  return (
    <PageShell title="管理" kicker="ADMIN" sections={ADMIN_SECTIONS} current={current}
      href={(id) => `?view=admin&section=${id}`} onSelect={(id) => go("admin", id)}>
      {current === "environments" && <EnvironmentsPanel />}
      {current === "users" && <UsersPanel />}
      {current === "teams" && <TeamPanel />}
      {current === "access" && <><TokensPanel /><CredentialsPanel /></>}
      {current === "quotas" && <QuotasPanel />}
    </PageShell>
  );
}
