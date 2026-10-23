import { ArrowLeft, Menu, Plus, Settings2, ShieldCheck } from "lucide-react";
import { api, setToken } from "@/api/client";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Brand } from "@/features/auth/LoginPage";
import { useConsole, type Page } from "@/features/console/context";
import { elevated } from "@/lib/access";
import { cn } from "@/lib/utils";

const PERSONAL = "__personal__";

export function TopBar({ onMenu }: { onMenu: () => void }) {
  const { me, teams, space, setSpace, open, page, go } = useConsole();
  const admin = elevated(me);

  async function logout() {
    try {
      await api("/logout", "POST", {});
    } finally {
      setToken("");
    }
  }

  // 管理 and 我的设置 are pages: their entry stays highlighted while it is open. On a phone they live in the account menu.
  const entry = (target: Page, label: string, Icon: typeof Settings2) => (
    <Button variant="ghost" size="sm" aria-current={page === target ? "page" : undefined} onClick={() => go(target)}
      className={cn("hidden sm:inline-flex", page === target && "bg-primary/10 text-primary hover:bg-primary/15 hover:text-primary")}>
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
    <header className="flex h-14 shrink-0 items-center gap-3 border-b bg-card px-3 sm:px-5">
      {page === "sessions" && (
        <Button variant="ghost" size="icon" className="md:hidden" onClick={onMenu} aria-label="打开会话列表">
          <Menu />
        </Button>
      )}
      <Brand compact />
      {page === "sessions" ? (
        <Select value={space || PERSONAL} onValueChange={(value) => setSpace(value === PERSONAL ? "" : value)}>
          <SelectTrigger aria-label="开发空间" className="ml-1 h-8 w-auto max-w-[52vw] min-w-32 sm:ml-4 sm:min-w-44">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={PERSONAL}>我的开发空间</SelectItem>
            {/* An archived Team stays here so its Sessions can still be read (P3). */}
            {teams.map((team) => <SelectItem key={team.id} value={team.id}>{team.name} · Team{team.archived ? " · 已归档" : ""}</SelectItem>)}
          </SelectContent>
        </Select>
      ) : (
        <Button variant="ghost" size="sm" className="ml-1 sm:ml-4" onClick={() => go("sessions")}>
          <ArrowLeft /> 返回会话
        </Button>
      )}
      <div className="ml-auto flex items-center gap-1.5 sm:gap-2">
        <Button size="sm" onClick={() => open({ kind: "launch", seed: {} })} id="new-run">
          <Plus /> <span className="hidden sm:inline">新建开发</span>
        </Button>
        {admin && entry("admin", "管理", ShieldCheck)}
        {entry("settings", "我的设置", Settings2)}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" className="rounded-full" aria-label={"账户 " + me.username}>
              <Avatar className="size-8"><AvatarFallback className="bg-primary/10 text-xs font-semibold text-primary">
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
