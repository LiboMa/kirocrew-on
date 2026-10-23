import type { ComponentProps, ReactNode } from "react";
import { RefreshCw, Search, type LucideIcon } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";

/** A list on the 管理 / 我的设置 pages (青岩, after Sub2API's list pages): a toolbar — a search when ``onSearch`` is given,
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

/** A row's action: an icon and its word, as in Sub2API's lists; ``name`` is the full accessible name. */
export function RowAction({ icon: Icon, children, name, tone, ...props }: ComponentProps<typeof Button> & {
  icon: LucideIcon; name: string; tone?: "danger";
}) {
  return (
    <Button type="button" size="xs" variant="ghost" aria-label={name}
      className={cn("text-muted-foreground hover:text-foreground", tone === "danger" && "hover:bg-destructive/10 hover:text-destructive")} {...props}>
      <Icon />{children}
    </Button>
  );
}
