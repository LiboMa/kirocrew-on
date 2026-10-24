import { MyEnvironmentsPanel } from "@/features/admin/EnvironmentsPanel";
import { useConsole } from "@/features/console/context";
import { PageShell } from "@/features/console/PageShell";
import { chosen, settingsSections } from "@/features/console/sections";
import { CredentialsPanel } from "@/features/settings/CredentialsPanel";
import { PasswordPanel } from "@/features/settings/PasswordPanel";
import { PoliciesPanel } from "@/features/settings/PoliciesPanel";
import { QuotasPanel } from "@/features/settings/QuotasPanel";
import { TeamPanel } from "@/features/settings/TeamPanel";
import { TokensPanel } from "@/features/settings/TokensPanel";

/** 我的设置: one's own access, credentials, preferences and password (the sections are in sections.ts). */
export function SettingsPage() {
  const { me, teams, section, go } = useConsole();
  const sections = settingsSections(me, teams, section);
  const current = chosen(sections, section).id;
  return (
    <PageShell title="我的设置" sections={sections} current={current} href={(id) => `?view=settings&section=${id}`}
      onSelect={(id) => go("settings", id)}>
      {current === "tokens" && <TokensPanel />}
      {current === "credentials" && <CredentialsPanel />}
      {current === "policies" && <PoliciesPanel />}
      {current === "team" && <TeamPanel />}
      {current === "environments" && <MyEnvironmentsPanel />}
      {current === "quotas" && <QuotasPanel />}
      {current === "password" && <PasswordPanel />}
    </PageShell>
  );
}
