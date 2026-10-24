import { EnvironmentsPanel } from "@/features/admin/EnvironmentsPanel";
import { OverviewPanel } from "@/features/admin/OverviewPanel";
import { UsersPanel } from "@/features/admin/UsersPanel";
import { useConsole } from "@/features/console/context";
import { PageShell } from "@/features/console/PageShell";
import { ADMIN_SECTIONS, chosen } from "@/features/console/sections";
import { CredentialsPanel } from "@/features/settings/CredentialsPanel";
import { QuotasPanel } from "@/features/settings/QuotasPanel";
import { TeamPanel } from "@/features/settings/TeamPanel";
import { TokensPanel } from "@/features/settings/TokensPanel";

/** 管理 (elevated administrators): every section of the platform's administration, one at a time. */
export function AdminPage() {
  const { section, go } = useConsole();
  const current = chosen(ADMIN_SECTIONS, section).id;
  return (
    <PageShell title="管理" sections={ADMIN_SECTIONS} current={current} href={(id) => `?view=admin&section=${id}`}
      onSelect={(id) => go("admin", id)}>
      {current === "overview" && <OverviewPanel />}
      {current === "environments" && <EnvironmentsPanel />}
      {current === "users" && <UsersPanel />}
      {current === "teams" && <TeamPanel />}
      {current === "access" && <><TokensPanel /><CredentialsPanel /></>}
      {current === "quotas" && <QuotasPanel />}
    </PageShell>
  );
}
