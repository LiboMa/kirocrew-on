import { FolderGit2, Gauge, KeyRound, Lock, Server, Shuffle, UsersRound } from "lucide-react";
import { EnvironmentsPanel } from "@/features/admin/EnvironmentsPanel";
import { useConsole } from "@/features/console/context";
import { PageShell, type PageSection } from "@/features/console/PageShell";
import { CredentialsPanel } from "@/features/settings/CredentialsPanel";
import { PasswordPanel } from "@/features/settings/PasswordPanel";
import { PoliciesPanel } from "@/features/settings/PoliciesPanel";
import { QuotasPanel } from "@/features/settings/QuotasPanel";
import { TeamPanel } from "@/features/settings/TeamPanel";
import { TokensPanel } from "@/features/settings/TokensPanel";
import { elevated } from "@/lib/access";

/** 我的设置: one's own access, credentials, preferences and password. Someone who is not an administrator also finds
 * here their Teams, their execution environments and limits (an administrator has them under 管理). */
export function SettingsPage() {
  const { me, teams, section, go } = useConsole();
  const admin = elevated(me);
  const sections: PageSection[] = [
    { id: "tokens", label: "API 与 Coding Agent 接入", icon: KeyRound },
    { id: "credentials", label: "代码仓库凭证", icon: FolderGit2 },
    { id: "policies", label: "映射偏好", icon: Shuffle },
    // Kept while open, so leaving one's last Team shows that one is in none now.
    ...(!admin && (teams.length || section === "team") ? [{ id: "team", label: "Team", icon: UsersRound }] : []),
    ...(!admin ? [{ id: "environments", label: "执行环境", icon: Server }, { id: "quotas", label: "并发上限", icon: Gauge }] : []),
    { id: "password", label: "修改密码", icon: Lock },
  ];
  const current = sections.some((s) => s.id === section) ? section : sections[0].id;
  return (
    <PageShell title="我的设置" kicker="SETTINGS" sections={sections} current={current}
      href={(id) => `?view=settings&section=${id}`} onSelect={(id) => go("settings", id)}>
      {current === "tokens" && <TokensPanel />}
      {current === "credentials" && <CredentialsPanel />}
      {current === "policies" && <PoliciesPanel />}
      {current === "team" && <TeamPanel />}
      {current === "environments" && <EnvironmentsPanel />}
      {current === "quotas" && <QuotasPanel />}
      {current === "password" && <PasswordPanel />}
    </PageShell>
  );
}
