import type { ReactNode } from "react";

/** The 管理 and 我的设置 pages' content: the chosen section, its navigation being the rail on the left. */
export function PageShell({ children }: { children: ReactNode }) {
  return (
    <main id="console-main" className="min-h-0 min-w-0 flex-1 overflow-y-auto">
      <div className="mx-auto max-w-6xl space-y-10 px-4 py-6 sm:px-8 sm:py-8">{children}</div>
    </main>
  );
}
