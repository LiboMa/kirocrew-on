import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { useMe, useWorkspace } from "@/api/hooks";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { ConsoleContext, type ConsoleState, type Dialog } from "@/features/console/context";
import { DialogHost } from "@/features/console/DialogHost";
import { EmptyState } from "@/features/console/EmptyState";
import { Sidebar } from "@/features/console/Sidebar";
import { TopBar } from "@/features/console/TopBar";
import { SessionView } from "@/features/sessions/SessionView";

const runFromUrl = () => new URLSearchParams(window.location.search).get("run");

export function Console() {
  const me = useMe();
  const workspace = useWorkspace();
  // An administrator sees every Team (D28) but can only switch to and submit in the Teams they belong to.
  const memberTeams = useMemo(() => workspace.teams.filter((t) => t.role != null), [workspace.teams]);
  const [space, setSpaceState] = useState("");
  const [selected, setSelected] = useState<string | null>(runFromUrl);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [navOpen, setNavOpen] = useState(false);

  const select = useCallback((id: string | null) => {
    setSelected(id);
    setNavOpen(false);
    window.history.replaceState(null, "", id ? "?run=" + encodeURIComponent(id) : "/");
  }, []);

  const setSpace = useCallback((value: string) => {
    setSpaceState(value);
    select(null);
  }, [select]);

  // A selected Session decides the space (like the classic console); a Session that is no longer readable is dropped.
  useEffect(() => {
    if (!selected || workspace.loading) return;
    const run = workspace.runs.find((r) => r.id === selected);
    if (!run) {
      select(null);
      toast.warning("该 Session 已不可访问。");
      return;
    }
    setSpaceState(run.team_id ?? "");
  }, [selected, workspace.runs, workspace.loading, select]);

  useEffect(() => {
    if (space && memberTeams.length && !memberTeams.some((t) => t.id === space)) setSpaceState("");
  }, [space, memberTeams]);

  const value = useMemo<ConsoleState | null>(() => me.data ? {
    me: me.data, teams: memberTeams, allTeams: workspace.teams, users: workspace.users, workers: workspace.workers,
    workflows: workspace.workflows, templates: workspace.templates, runs: workspace.runs,
    space, setSpace, selected, select, open: setDialog, close: () => setDialog(null),
  } : null, [me.data, workspace, space, setSpace, selected, select]);

  if (!value || workspace.loading) {
    return (
      <main className="grid min-h-svh place-items-center text-sm text-muted-foreground">
        {me.error || workspace.error ? (me.error ?? workspace.error)?.message : "正在加载开发空间…"}
      </main>
    );
  }

  return (
    <ConsoleContext.Provider value={value}>
      <div className="flex h-svh flex-col">
        <TopBar onMenu={() => setNavOpen(true)} />
        <div className="flex min-h-0 flex-1">
          <aside className="hidden w-80 shrink-0 border-r bg-sidebar md:flex md:flex-col">
            <Sidebar />
          </aside>
          <main className="min-w-0 flex-1 overflow-y-auto" id="console-main">
            {selected ? <SessionView key={selected} id={selected} /> : <EmptyState />}
          </main>
        </div>
      </div>
      <Sheet open={navOpen} onOpenChange={setNavOpen}>
        <SheetContent side="left" className="w-[88vw] max-w-sm gap-0 bg-sidebar p-0">
          <SheetTitle className="sr-only">会话与工作流</SheetTitle>
          <Sidebar />
        </SheetContent>
      </Sheet>
      <DialogHost dialog={dialog} />
    </ConsoleContext.Provider>
  );
}
