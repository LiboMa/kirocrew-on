import { useState, type MouseEvent, type ReactNode } from "react";
import { ChevronsLeft, ChevronsRight, MessagesSquare, Moon, Sun, type LucideIcon } from "lucide-react";
import { Logo } from "@/components/Logo";
import { useConsole, type Page } from "@/features/console/context";
import { ADMIN_SECTIONS, chosen, settingsSections } from "@/features/console/sections";
import { elevated } from "@/lib/access";
import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";

/** The console's navigation (owner 2026-10-24, 青岩): the brand; 工作台, 管理 (administrators) and 我的设置 as groups —
 * each a named navigation, so a section is found by its group — and at the bottom the theme and collapsing. Entries
 * are links to their addresses, so a section opens in a new tab or is bookmarked. ``collapsed`` keeps icons only. */
export function AppRail({ collapsed = false, onCollapse }: { collapsed?: boolean; onCollapse?: () => void }) {
  const { me, teams, page, section, go } = useConsole();
  const [theme, setTheme] = useTheme();
  const settings = settingsSections(me, teams, page === "settings" ? section : "");
  const current = (target: Page, id: string) => page === target && (target === "admin"
    ? chosen(ADMIN_SECTIONS, section).id === id : chosen(settings, section).id === id);

  return (
    <div className={cn("flex h-full flex-col bg-sidebar text-sidebar-foreground", collapsed ? "w-16" : "w-60")}>
      <div className={cn("flex h-14 shrink-0 items-center gap-2.5 border-b", collapsed ? "justify-center px-2" : "px-4")}>
        <Logo size={28} />
        {!collapsed && (
          <span className="leading-tight">
            <strong className="block text-sm tracking-wide">KiroCrew ON</strong>
            <small className="font-mono text-[11px] text-muted-foreground">开发工作台</small>
          </span>
        )}
      </div>
      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto px-2 py-4">
        <Group label="工作台" collapsed={collapsed}>
          <Entry icon={MessagesSquare} label="会话" href="?" active={page === "sessions"} collapsed={collapsed} onSelect={() => go("sessions")} />
        </Group>
        {elevated(me) && (
          <Group label="管理" collapsed={collapsed}>
            {ADMIN_SECTIONS.map((s) => (
              <Entry key={s.id} icon={s.icon} label={s.label} href={`?view=admin&section=${s.id}`} collapsed={collapsed}
                active={current("admin", s.id)} onSelect={() => go("admin", s.id)} />
            ))}
          </Group>
        )}
        <Group label="我的设置" collapsed={collapsed}>
          {settings.map((s) => (
            <Entry key={s.id} icon={s.icon} label={s.label} href={`?view=settings&section=${s.id}`} collapsed={collapsed}
              active={current("settings", s.id)} onSelect={() => go("settings", s.id)} />
          ))}
        </Group>
      </div>
      <div className="shrink-0 space-y-1 border-t p-2">
        <button type="button" onClick={() => setTheme(theme === "dark" ? "light" : "dark")} title={theme === "dark" ? "浅色模式" : "深色模式"}
          className={rowClass(false, collapsed)}>
          {theme === "dark" ? <Sun className="size-4 shrink-0" aria-hidden /> : <Moon className="size-4 shrink-0" aria-hidden />}
          <span className={cn(collapsed && "sr-only")}>{theme === "dark" ? "浅色模式" : "深色模式"}</span>
        </button>
        {onCollapse && (
          <button type="button" onClick={onCollapse} title={collapsed ? "展开导航" : "收起导航"} className={rowClass(false, collapsed)}>
            {collapsed ? <ChevronsRight className="size-4 shrink-0" aria-hidden /> : <ChevronsLeft className="size-4 shrink-0" aria-hidden />}
            <span className={cn(collapsed && "sr-only")}>{collapsed ? "展开导航" : "收起导航"}</span>
          </button>
        )}
      </div>
    </div>
  );
}

function Group({ label, collapsed, children }: { label: string; collapsed: boolean; children: ReactNode }) {
  return (
    <nav aria-label={label}>
      {/* Named by aria-label; not a heading, so a page's own titles stay its only ones */}
      <p aria-hidden className={cn("mb-1.5 px-3 text-xs font-medium text-muted-foreground", collapsed && "sr-only")}>{label}</p>
      <ul className="space-y-0.5">{children}</ul>
    </nav>
  );
}

const RAIL = "octopus.rail";

/** Whether the navigation is collapsed to its icons, kept across visits. */
export function useRailCollapsed(): [boolean, () => void] {
  const [collapsed, setCollapsed] = useState(() => {
    try {
      return localStorage.getItem(RAIL) === "collapsed";
    } catch {
      return false;
    }
  });
  const toggle = () => setCollapsed((was) => {
    try {
      localStorage.setItem(RAIL, was ? "open" : "collapsed");
    } catch {
      // a private window without storage: this visit only
    }
    return !was;
  });
  return [collapsed, toggle];
}

const rowClass = (active: boolean, collapsed: boolean) => cn(
  "flex w-full items-center gap-2.5 rounded-md px-3 py-2 text-sm outline-none transition-colors",
  "focus-visible:ring-[3px] focus-visible:ring-ring/50", collapsed && "justify-center px-0",
  active ? "bg-accent font-medium text-accent-foreground shadow-[inset_3px_0_0_var(--primary)]"
    : "text-muted-foreground hover:bg-muted hover:text-foreground");

function Entry({ icon: Icon, label, href, active, collapsed, onSelect }: {
  icon: LucideIcon; label: string; href: string; active: boolean; collapsed: boolean; onSelect: () => void;
}) {
  const follow = (event: MouseEvent<HTMLAnchorElement>) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return;
    event.preventDefault();
    onSelect();
  };
  return (
    <li>
      <a href={href} onClick={follow} aria-current={active ? "page" : undefined} title={collapsed ? label : undefined}
        className={rowClass(active, collapsed)}>
        <Icon className="size-4 shrink-0" aria-hidden />
        <span className={cn("truncate", collapsed && "sr-only")}>{label}</span>
      </a>
    </li>
  );
}
