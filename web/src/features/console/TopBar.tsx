import type { MouseEvent } from "react";
import { ArrowLeft, ListTree, Moon, Plus, Settings2, ShieldCheck, Sun } from "lucide-react";
import { api, setToken } from "@/api/client";
import { Logo } from "@/components/Logo";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useConsole, type Page } from "@/features/console/context";
import { elevated } from "@/lib/access";
import { useTheme } from "@/lib/theme";
import { cn } from "@/lib/utils";

const PERSONAL = "__personal__";

/** The top bar (owner 2026-10-24, 方案一「顶栏切换」): the workbench comes first — the space and 「会话 / 工作流」 in the
 * middle; 新建开发 beside them; 管理 and 我的设置 are quiet words on the right (in the account menu on a phone), and their
 * pages offer 「返回工作台」 in the place of the workbench. */
export function TopBar({ onList }: { onList: () => void }) {
  const { me, teams, space, setSpace, open, page, go, spaceTotal, workflows } = useConsole();
  const admin = elevated(me);
  const [theme, setTheme] = useTheme();
  const workbench = page === "sessions" || page === "workflows";

  async function logout() {
    try {
      await api("/logout", "POST", {});
    } finally {
      setToken("");
    }
  }

  // 管理 and 我的设置 are pages: their entry stays marked while it is open. On a phone they live in the account menu.
  const entry = (target: Page, label: string, Icon: typeof Settings2) => (
    <Button variant="ghost" size="sm" aria-current={page === target ? "page" : undefined} onClick={() => go(target)}
      className={cn("hidden text-muted-foreground sm:inline-flex", page === target && "bg-accent text-accent-foreground")}>
      <Icon /> {label}
    </Button>
  );

  return (
    <>
    {!!me.admin && !admin && (
      // A login from before the admin scope existed: administration answers 403 until the admin signs in again.
      <div role="status" className="flex items-center gap-3 border-b border-warning/30 bg-warning/10 px-4 py-1.5 text-xs">
        <span className="flex-1">你的登录早于管理员权限更新，重新登录后才能使用管理功能。</span>
        <Button size="sm" variant="outline" onClick={() => void logout()}>重新登录</Button>
      </div>
    )}
    <header className="flex h-14 shrink-0 items-center gap-1 border-b bg-card px-2 sm:gap-3 sm:px-5">
      {page === "sessions" && (
        <Button variant="ghost" size="icon" className="md:hidden" onClick={onList} aria-label="打开会话列表">
          <ListTree />
        </Button>
      )}
      <span className="hidden shrink-0 items-center gap-2.5 sm:flex">   {/* a phone needs the room for the workbench */}
        <Logo size={26} />
        <strong className="hidden text-sm tracking-wide lg:inline">KiroCrew ON</strong>
      </span>
      {workbench ? (
        <>
          <Select value={space || PERSONAL} onValueChange={(value) => setSpace(value === PERSONAL ? "" : value)}>
            <SelectTrigger aria-label="开发空间" className="h-8 w-[30vw] min-w-0 sm:ml-2 sm:w-auto sm:max-w-[40vw] sm:min-w-40">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              <SelectItem value={PERSONAL}>我的开发空间</SelectItem>
              {/* An archived Team stays here so its Sessions can still be read (P3). */}
              {teams.map((team) => <SelectItem key={team.id} value={team.id}>{team.name} · Team{team.archived ? " · 已归档" : ""}</SelectItem>)}
            </SelectContent>
          </Select>
          <nav aria-label="工作台" className="flex gap-0.5 rounded-lg bg-muted p-0.5 sm:ml-3">
            <WorkbenchTab label="会话" count={spaceTotal} href="?" active={page === "sessions"} onSelect={() => go("sessions")} />
            <WorkbenchTab label="工作流" count={workflows.length} href="?view=workflows" active={page === "workflows"}
              onSelect={() => go("workflows")} />
          </nav>
        </>
      ) : (
        <Button variant="ghost" size="sm" className="ml-1 sm:ml-3" onClick={() => go("sessions")}>
          <ArrowLeft /> 返回工作台
        </Button>
      )}
      <div className="ml-auto flex items-center gap-1 sm:gap-1.5">
        <Button size="sm" onClick={() => open({ kind: "launch", seed: {} })} id="new-run" className="mr-1">
          <Plus /> <span className="hidden sm:inline">新建开发</span>
        </Button>
        {admin && entry("admin", "管理", ShieldCheck)}
        {entry("settings", "我的设置", Settings2)}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" aria-label={"账户 " + me.username}>
              <Avatar className="size-8 rounded-md"><AvatarFallback className="rounded-md bg-primary/10 text-xs font-semibold text-primary">
                {me.username.slice(0, 2).toUpperCase()}
              </AvatarFallback></Avatar>
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuLabel className="font-normal">
              <strong className="block">{me.username}</strong>
              <small className="text-muted-foreground">{me.admin ? "平台管理员" : "平台成员"}</small>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            {admin && <DropdownMenuItem className="sm:hidden" onSelect={() => go("admin")}>管理</DropdownMenuItem>}
            <DropdownMenuItem className="sm:hidden" onSelect={() => go("settings")}>我的设置</DropdownMenuItem>
            <DropdownMenuItem onSelect={() => go("settings", "password")}>修改密码</DropdownMenuItem>
            <DropdownMenuItem onSelect={() => setTheme(theme === "dark" ? "light" : "dark")}>
              {theme === "dark" ? <Sun /> : <Moon />}{theme === "dark" ? "浅色模式" : "深色模式"}
            </DropdownMenuItem>
            <DropdownMenuItem asChild><a href="/classic/">打开经典控制台</a></DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={() => void logout()}>退出登录</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
    </>
  );
}

/** 「会话」 or 「工作流」: a link to its address, so it opens in a new tab too; the open one is marked. */
function WorkbenchTab({ label, count, href, active, onSelect }: {
  label: string; count: number; href: string; active: boolean; onSelect: () => void;
}) {
  const follow = (event: MouseEvent<HTMLAnchorElement>) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return;
    event.preventDefault();
    onSelect();
  };
  return (
    <a href={href} onClick={follow} aria-current={active ? "page" : undefined}
      className={cn("rounded-md px-2.5 py-1 text-sm font-medium whitespace-nowrap outline-none transition-colors sm:px-4",
        "focus-visible:ring-[3px] focus-visible:ring-ring/50",
        active ? "bg-card text-foreground shadow-[inset_0_-2px_0_var(--primary)]" : "text-muted-foreground hover:text-foreground")}>
      {label} <span className="ml-0.5 hidden text-xs font-normal text-muted-foreground sm:inline">{count}</span>
    </a>
  );
}
