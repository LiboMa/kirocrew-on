import { FolderGit2, Gauge, KeyRound, LayoutDashboard, Lock, Server, Shuffle, Users, UsersRound, type LucideIcon } from "lucide-react";
import type { Me, Team } from "@/api/types";
import { elevated } from "@/lib/access";

/** A section of 管理 or 我的设置: its address part and its name in the navigation and the page header. */
export interface PageSection {
  id: string;
  label: string;
  icon: LucideIcon;
}

export const ADMIN_SECTIONS: PageSection[] = [
  { id: "overview", label: "概览", icon: LayoutDashboard },
  { id: "environments", label: "执行环境", icon: Server },
  { id: "users", label: "用户", icon: Users },
  { id: "teams", label: "Team", icon: UsersRound },
  { id: "access", label: "接入与凭证", icon: KeyRound },
  { id: "quotas", label: "并发上限", icon: Gauge },
];

/** 我的设置: one's own access, credentials, preferences and password; someone who is not an administrator also finds here
 * their Teams, execution environments and limits (an administrator has them under 管理). ``open`` keeps the Team entry
 * while it is open, so leaving one's last Team shows that one is in none now. */
export function settingsSections(me: Me, teams: Team[], open = ""): PageSection[] {
  const admin = elevated(me);
  return [
    { id: "tokens", label: "API 与 Coding Agent 接入", icon: KeyRound },
    { id: "credentials", label: "代码仓库凭证", icon: FolderGit2 },
    { id: "policies", label: "映射偏好", icon: Shuffle },
    ...(!admin && (teams.length || open === "team")
      ? [{ id: "team", label: "Team", icon: UsersRound }] : []),
    ...(!admin ? [
      { id: "environments", label: "执行环境", icon: Server },
      { id: "quotas", label: "并发上限", icon: Gauge },
    ] : []),
    { id: "password", label: "修改密码", icon: Lock },
  ];
}

/** The section to show: the one asked for when it exists, else the first. */
export function chosen(sections: PageSection[], id: string): PageSection {
  return sections.find((s) => s.id === id) ?? sections[0];
}
