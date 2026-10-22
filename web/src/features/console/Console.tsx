import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { useMe, useRun, useRunCount, useRunPages, useWorkspace } from "@/api/hooks";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { ConsoleContext, type ConsoleState, type Dialog } from "@/features/console/context";
import { DialogHost } from "@/features/console/DialogHost";
import { EmptyState } from "@/features/console/EmptyState";
import { Sidebar } from "@/features/console/Sidebar";
import { TopBar } from "@/features/console/TopBar";
import { SessionView } from "@/features/sessions/SessionView";
import { elevated } from "@/lib/access";
import { EMPTY_FILTERS, filterParams, filtersFromUrl, listParams, writeUrl, type RunListFilters } from "@/lib/runList";

const runFromUrl = () => new URLSearchParams(window.location.search).get("run");

export function Console() {
  const me = useMe();
  const [allSessions, setAllSessions] = useState(false);
  const workspace = useWorkspace();
  // An administrator sees every Team (D28) but can only switch to and submit in the Teams they belong to.
  const memberTeams = useMemo(() => workspace.teams.filter((t) => t.role != null), [workspace.teams]);
  const [space, setSpaceState] = useState("");
  const [selected, setSelected] = useState<string | null>(runFromUrl);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [navOpen, setNavOpen] = useState(false);
  // D56: the list is filtered and paged on the server; the filters live in the address bar too.
  const [filters, setFiltersState] = useState<RunListFilters>(() => filtersFromUrl(window.location.search));
  const scope = allSessions ? null : space;
  const pages = useRunPages(listParams(filters, scope), allSessions);
  const count = useRunCount(listParams(EMPTY_FILTERS, scope, 1), allSessions);
  const runs = useMemo(() => pages.data?.pages.flatMap((page) => page.items) ?? [], [pages.data]);
  const chosen = useRun(selected);

  const select = useCallback((id: string | null) => {
    setSelected(id);
    setNavOpen(false);
    writeUrl({ run: id });
  }, []);

  const setFilters = useCallback((next: RunListFilters) => {
    setFiltersState(next);
    writeUrl(filterParams(next));
  }, []);

  const setSpace = useCallback((value: string) => {
    setSpaceState(value);
    select(null);
  }, [select]);

  // A selected Session decides the space (like the classic console); one beyond the loaded pages is read by its id, and
  // only one the server refuses (404 / 403) is dropped — not on a passing network error.
  useEffect(() => {
    if (!selected || workspace.loading || pages.isPending) return;
    if (chosen.error instanceof ApiError && [403, 404].includes(chosen.error.status)) {   // before any copy read earlier
      select(null);
      toast.warning("该 Session 已不可访问。");
      return;
    }
    const run = runs.find((r) => r.id === selected) ?? chosen.data;
    if (run) {
      const theirs = run.team_id ? !memberTeams.some((t) => t.id === run.team_id) : run.owner_id !== me.data?.id;
      if (theirs && me.data && elevated(me.data) && !allSessions) {
        setAllSessions(true);   // an administrator's link to someone else's Session: show it among every Session
        return;
      }
      setSpaceState(run.team_id ?? "");
    }
  }, [selected, runs, chosen.data, chosen.error, workspace.loading, pages.isPending, select, me.data, allSessions, memberTeams]);

  useEffect(() => {
    // Also after leaving one's only Team: once the Teams are loaded, a space that is not among them is dropped.
    if (space && !workspace.loading && !memberTeams.some((t) => t.id === space)) setSpaceState("");
  }, [space, memberTeams, workspace.loading]);

  const value = useMemo<ConsoleState | null>(() => me.data ? {
    me: me.data, teams: memberTeams, allTeams: workspace.teams, users: workspace.users, workers: workspace.workers,
    workflows: workspace.workflows, templates: workspace.templates, runs,
    runTotal: pages.data?.pages[0]?.total ?? 0, spaceTotal: count.data?.total ?? 0, hasMore: !!pages.hasNextPage,
    listError: pages.error?.message ?? null,
    loadMore: () => void pages.fetchNextPage(), loadingMore: pages.isFetchingNextPage, filters, setFilters,
    space, setSpace, allSessions, setAllSessions, selected, select, open: setDialog, close: () => setDialog(null),
  } : null, [me.data, workspace, runs, pages, count.data, filters, setFilters, space, setSpace, allSessions, selected, select]);

  if (!value || workspace.loading || pages.isPending) {
    return (
      <main className="grid min-h-svh place-items-center text-sm text-muted-foreground">
        {me.error || workspace.error || pages.error ? (me.error ?? workspace.error ?? pages.error)?.message : "正在加载开发空间…"}
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
