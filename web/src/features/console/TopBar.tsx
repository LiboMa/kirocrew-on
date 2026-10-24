import { ListTree, Menu, Plus } from "lucide-react";
import { api, setToken } from "@/api/client";
import { Logo } from "@/components/Logo";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useConsole } from "@/features/console/context";
import { ADMIN_SECTIONS, chosen, settingsSections } from "@/features/console/sections";
import { elevated } from "@/lib/access";

const PERSONAL = "__personal__";

/** The page header (青岩): where one is — the space on the Sessions page, 管理 / 我的设置 and the section elsewhere — then
 * 新建开发 and the account. The navigation itself is the rail on the left; on a narrow screen it opens from 「打开导航」,
 * and the Session list from 「打开会话列表」. */
export function TopBar({ onNav, onList }: { onNav: () => void; onList: () => void }) {
  const { me, teams, space, setSpace, open, page, section, go } = useConsole();
  const admin = elevated(me);

  async function logout() {
    try {
      await api("/logout", "POST", {});
    } finally {
      setToken("");
    }
  }

  const place = page === "admin" ? ["管理", chosen(ADMIN_SECTIONS, section).label]
    : page === "settings" ? ["我的设置", chosen(settingsSections(me, teams, section), section).label] : null;

  return (
    <>
    {!!me.admin && !admin && (
      // A login from before the admin scope existed: administration answers 403 until the admin signs in again.
      <div role="status" className="flex items-center gap-3 border-b border-warning/30 bg-warning/10 px-4 py-1.5 text-xs">
        <span className="flex-1">你的登录早于管理员权限更新，重新登录后才能使用管理功能。</span>
        <Button size="sm" variant="outline" onClick={() => void logout()}>重新登录</Button>
      </div>
    )}
    <header className="flex h-14 shrink-0 items-center gap-2 border-b bg-card px-3 sm:gap-3 sm:px-5">
      <Button variant="ghost" size="icon" className="lg:hidden" onClick={onNav} aria-label="打开导航">
        <Menu />
      </Button>
      <Logo size={24} className="shrink-0 lg:hidden" />
      {page === "sessions" && (
        <Button variant="ghost" size="icon" className="md:hidden" onClick={onList} aria-label="打开会话列表">
          <ListTree />
        </Button>
      )}
      {place ? (
        // The page's one level-1 heading (a Session's page has its own): 管理 / 用户
        <h1 className="min-w-0 truncate text-sm font-normal">
          <span className="text-muted-foreground">{place[0]}</span>
          <span aria-hidden className="mx-2 text-muted-foreground/60">/</span>
          <strong className="font-medium">{place[1]}</strong>
        </h1>
      ) : (
        <Select value={space || PERSONAL} onValueChange={(value) => setSpace(value === PERSONAL ? "" : value)}>
          <SelectTrigger aria-label="开发空间" className="h-8 w-auto max-w-[52vw] min-w-32 sm:min-w-44">
            <SelectValue />
          </SelectTrigger>
          <SelectContent>
            <SelectItem value={PERSONAL}>我的开发空间</SelectItem>
            {/* An archived Team stays here so its Sessions can still be read (P3). */}
            {teams.map((team) => <SelectItem key={team.id} value={team.id}>{team.name} · Team{team.archived ? " · 已归档" : ""}</SelectItem>)}
          </SelectContent>
        </Select>
      )}
      <div className="ml-auto flex items-center gap-1.5 sm:gap-2">
        <Button size="sm" onClick={() => open({ kind: "launch", seed: {} })} id="new-run">
          <Plus /> <span className="hidden sm:inline">新建开发</span>
        </Button>
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
