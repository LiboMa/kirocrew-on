import { useRef, useSyncExternalStore, type ReactNode } from "react";
import { MoreHorizontal, RefreshCw, Search, type LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

/** A list on the 管理 / 我的设置 pages (青岩): a toolbar — a search when ``onSearch`` is given,
 * 刷新 when ``onRefresh`` is, what the list holds on the right — above the rows (a table, or what to do when empty). */
export function ListCard({ label, search, onSearch, searchLabel = "搜索", onRefresh, refreshing, summary, children }: {
  /** Names the list for assistive technology. */
  label: string;
  search?: string;
  onSearch?: (value: string) => void;
  searchLabel?: string;
  onRefresh?: () => void;
  refreshing?: boolean;
  summary?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div role="region" aria-label={label} className="overflow-hidden rounded-lg border bg-card">
      {(onSearch || onRefresh || summary) && (
        <div className="flex flex-wrap items-center gap-2 border-b px-3 py-2.5">
          {onSearch && (
            <div className="relative w-full max-w-xs">
              <Search aria-hidden className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input type="search" value={search ?? ""} onChange={(e) => onSearch(e.target.value)} aria-label={searchLabel}
                placeholder={searchLabel} className="h-8 pl-8 text-sm" />
            </div>
          )}
          {onRefresh && (
            <Button type="button" size="icon-sm" variant="outline" onClick={onRefresh} aria-label="刷新" title="刷新" disabled={refreshing}>
              <RefreshCw className={cn(refreshing && "animate-spin motion-reduce:animate-none")} />
            </Button>
          )}
          {summary && <span className="ml-auto text-xs text-muted-foreground">{summary}</span>}
        </div>
      )}
      {children}
    </div>
  );
}

/** What an empty (or emptied by the search) list says, and what to do. */
export function ListEmpty({ children }: { children: ReactNode }) {
  return <p className="px-4 py-10 text-center text-sm text-muted-foreground">{children}</p>;
}

export interface Action {
  icon: LucideIcon;
  /** The word on the button and in the menu (「重新生成」). */
  label: string;
  /** The full accessible name of the row's button (「重新生成 Token：claude MCP」). */
  name: string;
  onSelect: () => void;
  tone?: "danger";
  disabled?: boolean;
  /** Runs at once rather than opening a dialog (刷新模型): its menu item has no 「…」. */
  direct?: boolean;
}

const NARROW = "(max-width: 639px)";
const narrowNow = () => typeof window !== "undefined" && !!window.matchMedia?.(NARROW).matches;
function watchNarrow(listener: () => void) {
  const query = window.matchMedia?.(NARROW);
  query?.addEventListener?.("change", listener);
  return () => query?.removeEventListener?.("change", listener);
}

/** A row's actions (PRD 2026-10-23 §3.5): ``actions`` as an icon and its word beside each other on a
 * wide screen; ``more`` — and on a phone everything — in the row's 「⋯」 menu, named ``menu``. */
export function RowActions({ actions, more = [], menu }: { actions: Action[]; more?: Action[]; menu: string }) {
  const narrow = useSyncExternalStore(watchNarrow, narrowNow, () => false);
  const inline = narrow ? [] : actions;
  const listed = narrow ? [...actions, ...more] : more;
  // A menu item's step runs once the menu has closed and given the focus back to 「⋯」: a dialog it opens then returns
  // the focus there when it closes, not to the top of the page (the menu item it would remember is gone by then).
  const chosen = useRef<(() => void) | null>(null);
  return (
    <span className="inline-flex items-center justify-end gap-1">
      {inline.map((a) => (
        <Button key={a.label} type="button" size="xs" variant="ghost" aria-label={a.name} onClick={a.onSelect} disabled={a.disabled}
          className={cn("text-muted-foreground hover:text-foreground", a.tone === "danger" && "hover:bg-destructive/10 hover:text-destructive")}>
          <a.icon />{a.label}
        </Button>
      ))}
      {listed.length > 0 && (
        <DropdownMenu onOpenChange={(open) => { if (open) chosen.current = null; }}>   {/* reopened while closing: forget the old step */}
          <DropdownMenuTrigger asChild>
            <Button type="button" size="icon-sm" variant="ghost" aria-label={menu}><MoreHorizontal /></Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-44" onCloseAutoFocus={() => {
            const step = chosen.current;
            chosen.current = null;
            if (step) window.setTimeout(step);
          }}>
            {listed.map((a, index) => [
              // One rule sets the dangerous ones apart, not one between each of them
              a.tone === "danger" && index > 0 && listed[index - 1].tone !== "danger" && <DropdownMenuSeparator key={a.label + "-rule"} />,
              <DropdownMenuItem key={a.label} onSelect={() => { chosen.current = a.onSelect; }} disabled={a.disabled}
                className={cn(a.tone === "danger" && "text-destructive")}>
                <a.icon />{a.label}{a.direct ? "" : "…"}
              </DropdownMenuItem>,
            ])}
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </span>
  );
}
