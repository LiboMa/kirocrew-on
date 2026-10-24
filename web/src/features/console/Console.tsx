import { useCallback, useEffect, useMemo, useState } from "react";
import { toast } from "sonner";
import { ApiError } from "@/api/client";
import { useMe, useRun, useRunCount, useRunPages, useWorkspace } from "@/api/hooks";
import { Sheet, SheetContent, SheetTitle } from "@/components/ui/sheet";
import { AdminPage } from "@/features/admin/AdminPage";
import { ConsoleContext, type ConsoleState, type Dialog, type Page } from "@/features/console/context";
import { DialogHost } from "@/features/console/DialogHost";
import { EmptyState } from "@/features/console/EmptyState";
import { Sidebar } from "@/features/console/Sidebar";
import { TopBar } from "@/features/console/TopBar";
import { SessionView } from "@/features/sessions/SessionView";
import { SettingsPage } from "@/features/settings/SettingsPage";
import { WorkflowsPage } from "@/features/workflows/WorkflowsPage";
import { elevated } from "@/lib/access";
import { EMPTY_FILTERS, filterParams, filtersFromUrl, listParams, writeUrl, type RunListFilters } from "@/lib/runList";

const runFromUrl = () => new URLSearchParams(window.location.search).get("run");
const PAGES: Page[] = ["sessions", "workflows", "admin", "settings"];
const pageFromUrl = (): Page => {
  const view = new URLSearchParams(window.location.search).get("view") as Page | null;
  return view && PAGES.includes(view) ? view : "sessions";
};
const sectionFromUrl = () => new URLSearchParams(window.location.search).get("section") ?? "";

export function Console() {
  const me = useMe();
  const [allSessions, setAllState] = useState(false);
  const [sharedSessions, setSharedState] = useState(false);   // D64: what others shared with the user
  const setAllSessions = useCallback((all: boolean) => { setAllState(all); if (all) setSharedState(false); }, []);
  const setSharedSessions = useCallback((shared: boolean) => { setSharedState(shared); if (shared) setAllState(false); }, []);
  const workspace = useWorkspace();
  // An administrator sees every Team (D28) but can only switch to and submit in the Teams they belong to.
  const memberTeams = useMemo(() => workspace.teams.filter((t) => t.role != null), [workspace.teams]);
  const [space, setSpaceState] = useState("");
  const [selected, setSelected] = useState<string | null>(runFromUrl);
  // The 管理 and 我的设置 pages live in the address bar too, so a refresh or a shared link opens the same section.
  const [page, setPage] = useState<Page>(pageFromUrl);
  const [section, setSection] = useState(sectionFromUrl);
  const [dialog, setDialog] = useState<Dialog | null>(null);
  const [navOpen, setNavOpen] = useState(false);
  // D56: the list is filtered and paged on the server; the filters live in the address bar too.
  const [filters, setFiltersState] = useState<RunListFilters>(() => filtersFromUrl(window.location.search));
  const scope = allSessions || sharedSessions ? null : space;
  const shared = sharedSessions ? "&shared=1" : "";
  const pages = useRunPages(listParams(filters, scope) + shared, allSessions);
  const count = useRunCount(listParams(EMPTY_FILTERS, scope, 1) + shared, allSessions);
  const runs = useMemo(() => pages.data?.pages.flatMap((page) => page.items) ?? [], [pages.data]);
  const chosen = useRun(selected);

  const select = useCallback((id: string | null) => {
    setSelected(id);
    setNavOpen(false);
    if (!id) return writeUrl({ run: null });
    setPage("sessions");   // opening a Session (a new one too) shows it, whichever page was open
    writeUrl({ run: id, view: null, section: null });
  }, []);

  const go = useCallback((next: Page, part = "") => {
    setPage(next);
    setSection(part);
    setNavOpen(false);
    writeUrl(next === "sessions" ? { view: null, section: null } : { view: next, section: part || null });
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
    space, setSpace, allSessions, setAllSessions, sharedSessions, setSharedSessions, selected, select, open: setDialog,
    close: () => setDialog(null),
    // 管理 is for administrators only: anyone else following such a link sees their Sessions.
    page: page === "admin" && !elevated(me.data) ? "sessions" : page, section, go,
  } : null, [me.data, workspace, runs, pages, count.data, filters, setFilters, space, setSpace, allSessions, setAllSessions,
    sharedSessions, setSharedSessions, selected, select, page, section, go]);

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
        <TopBar onList={() => setNavOpen(true)} />
        {value.page === "admin" ? <AdminPage /> : value.page === "settings" ? <SettingsPage />
          : value.page === "workflows" ? <WorkflowsPage /> : (
          <div className="flex min-h-0 flex-1">
            <aside className="hidden w-80 shrink-0 border-r bg-sidebar md:flex md:flex-col">
              <Sidebar />
            </aside>
            <main className="min-w-0 flex-1 overflow-y-auto" id="console-main">
              {selected ? <SessionView key={selected} id={selected} /> : <EmptyState />}
            </main>
          </div>
        )}
      </div>
      <Sheet open={navOpen} onOpenChange={setNavOpen}>
        <SheetContent side="left" className="w-[88vw] max-w-sm gap-0 bg-sidebar p-0">
          <SheetTitle className="sr-only">会话列表</SheetTitle>
          <Sidebar />
        </SheetContent>
      </Sheet>
      <DialogHost dialog={dialog} />
    </ConsoleContext.Provider>
  );
}
