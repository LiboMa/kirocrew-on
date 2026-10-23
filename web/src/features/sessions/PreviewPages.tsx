import { useState } from "react";
import { ChevronDown } from "lucide-react";
import { api } from "@/api/client";
import type { Run } from "@/api/types";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

type Page = { path: string; url: string };

/** Every HTML page the Session produced (EC2 2026-10-22: reports in report/ and artifacts/<name>/), each opened with the
 * Session's ten-minute preview link; 「预览产出」 next to it opens the best one. */
export function PreviewPages({ run }: { run: Run }) {
  const [pages, setPages] = useState<Page[] | null>(null);
  const [error, setError] = useState("");

  const load = (open: boolean) => {
    if (!open) return;
    setPages(null);
    setError("");
    api<{ pages?: Page[] }>("/runs/" + encodeURIComponent(run.id) + "/preview", "POST", {})
      .then((result) => setPages(result.pages ?? []))
      .catch((reason: Error) => setError(reason.message));
  };

  return (
    <DropdownMenu onOpenChange={load}>
      <DropdownMenuTrigger asChild>
        <Button size="sm" variant="outline" className="px-2" disabled={!run.worker_id} aria-label="选择预览页面" id="run-preview-pages">
          <ChevronDown />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="max-w-96">
        <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">本会话产出的页面</DropdownMenuLabel>
        {error && <p role="alert" className="px-2 py-1.5 text-sm text-destructive">{error}</p>}
        {!error && pages === null && <p className="px-2 py-1.5 text-sm text-muted-foreground">正在读取…</p>}
        {pages?.map((page) => (
          <DropdownMenuItem key={page.path} onSelect={() => window.open(page.url, "_blank", "noopener")}>
            <span className="truncate font-mono text-xs">{page.path}</span>
          </DropdownMenuItem>
        ))}
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
