import { useState } from "react";
import { FileText } from "lucide-react";
import { toast } from "sonner";
import { apiText } from "@/api/client";
import type { Run } from "@/api/types";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";

function save(text: string, name: string, kind: string) {
  const url = URL.createObjectURL(new Blob([text], { type: kind }));
  const link = document.createElement("a");
  link.href = url;
  link.download = name;
  link.click();
  URL.revokeObjectURL(url);
}

/** The Session's Feature report (D37 phase 2): shown sandboxed (no scripts; its pull-request link opens a new tab), and
 * downloadable as Markdown or as one HTML file. */
export function ReportButton({ run }: { run: Run }) {
  const [page, setPage] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const path = (format: "md" | "html") => `/runs/${encodeURIComponent(run.id)}/report?format=${format}`;
  const show = async () => {
    setOpen(true);
    setPage(null);
    try {
      setPage(await apiText(path("html")));
    } catch (error) {
      setOpen(false);
      toast.error((error as Error).message);
    }
  };
  const download = async (format: "md" | "html") => {
    try {
      const text = format === "html" && page !== null ? page : await apiText(path(format));
      save(text, `${run.id}-report.${format}`, format === "html" ? "text/html" : "text/markdown");
    } catch (error) {
      toast.error((error as Error).message);
    }
  };
  return (
    <>
      <Button size="sm" variant="outline" onClick={show} id="run-report">
        <FileText /> 报告
      </Button>
      <Dialog open={open} onOpenChange={setOpen}>
        <DialogContent className="max-h-[94svh] sm:max-w-5xl">
          <DialogHeader>
            <DialogTitle>会话报告</DialogTitle>
            <DialogDescription>需求与计划、各任务的门禁与候选、审查结论、纠错记录与发布</DialogDescription>
          </DialogHeader>
          {page === null ? <p className="text-sm text-muted-foreground">正在生成报告…</p> : (
            <iframe title="会话报告" sandbox="allow-popups allow-popups-to-escape-sandbox" srcDoc={page}
              className="h-[70svh] w-full rounded-md border bg-white" />
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => download("md")}>下载 Markdown</Button>
            <Button type="button" variant="outline" onClick={() => download("html")}>下载 HTML</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
