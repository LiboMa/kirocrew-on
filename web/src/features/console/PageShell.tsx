import type { MouseEvent, ReactNode } from "react";
import type { PageSection } from "@/features/console/sections";
import { cn } from "@/lib/utils";

/** The 管理 and 我的设置 pages (owner 2026-10-24: as before the workbench came first): their own sections on the left (a
 * row above on a narrow screen), the chosen one on the right. Each entry is a link to its address, so a section can be
 * opened in a new tab or bookmarked. */
export function PageShell({ title, sections, current, href, onSelect, children }: {
  title: string;
  sections: PageSection[];
  current: string;
  href: (id: string) => string;
  onSelect: (id: string) => void;
  children: ReactNode;
}) {
  const follow = (id: string) => (event: MouseEvent<HTMLAnchorElement>) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey || event.button !== 0) return;
    event.preventDefault();
    onSelect(id);
  };
  return (
    <div className="flex min-h-0 flex-1 flex-col md:flex-row">
      <nav aria-label={title} className="shrink-0 border-b bg-sidebar md:w-60 md:border-r md:border-b-0">
        <h1 className="sr-only md:not-sr-only md:block md:px-5 md:pt-5 md:pb-3 md:text-base md:font-semibold">{title}</h1>
        <ul className="flex gap-1 overflow-x-auto px-3 py-2 md:flex-col md:overflow-visible md:py-0">
          {sections.map(({ id, label, icon: Icon }) => (
            <li key={id} className="shrink-0">
              <a href={href(id)} onClick={follow(id)} aria-current={id === current ? "page" : undefined}
                className={cn("flex items-center gap-2.5 rounded-md px-3 py-2 text-sm whitespace-nowrap outline-none transition-colors md:whitespace-normal",
                  "focus-visible:ring-[3px] focus-visible:ring-ring/50",
                  id === current ? "bg-accent font-medium text-accent-foreground shadow-[inset_3px_0_0_var(--primary)]"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground")}>
                <Icon className="size-4 shrink-0" aria-hidden />
                {label}
              </a>
            </li>
          ))}
        </ul>
      </nav>
      <main id="console-main" className="min-w-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-6xl space-y-10 px-4 py-6 sm:px-8 sm:py-8">{children}</div>
      </main>
    </div>
  );
}
