import { Menu, Plus, Settings2 } from "lucide-react";
import { api, setToken } from "@/api/client";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Brand } from "@/features/auth/LoginPage";
import { useConsole } from "@/features/console/context";

const PERSONAL = "__personal__";

export function TopBar({ onMenu }: { onMenu: () => void }) {
  const { me, teams, space, setSpace, open } = useConsole();

  async function logout() {
    try {
      await api("/logout", "POST", {});
    } finally {
      setToken("");
    }
  }

  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b bg-card px-3 sm:px-5">
      <Button variant="ghost" size="icon" className="md:hidden" onClick={onMenu} aria-label="打开会话列表">
        <Menu />
      </Button>
      <Brand compact />
      <Select value={space || PERSONAL} onValueChange={(value) => setSpace(value === PERSONAL ? "" : value)}>
        <SelectTrigger aria-label="开发空间" className="ml-1 h-8 w-auto max-w-[52vw] min-w-32 sm:ml-4 sm:min-w-44">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          <SelectItem value={PERSONAL}>我的开发空间</SelectItem>
          {teams.map((team) => <SelectItem key={team.id} value={team.id}>{team.name} · Team</SelectItem>)}
        </SelectContent>
      </Select>
      <div className="ml-auto flex items-center gap-1.5 sm:gap-2">
        <Button size="sm" onClick={() => open({ kind: "launch", seed: {} })} id="new-run">
          <Plus /> <span className="hidden sm:inline">新建开发</span>
        </Button>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" aria-label="设置"><Settings2 /></Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-52">
            <DropdownMenuItem onSelect={() => open({ kind: "team" })}>团队与成员</DropdownMenuItem>
            <DropdownMenuItem onSelect={() => open({ kind: "tokens" })}>API 与 Coding Agent 接入</DropdownMenuItem>
            <DropdownMenuItem onSelect={() => open({ kind: "workers" })}>执行环境 · Worker</DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem asChild><a href="/classic/">打开经典控制台</a></DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
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
            <DropdownMenuItem onSelect={() => void logout()}>退出登录</DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}
