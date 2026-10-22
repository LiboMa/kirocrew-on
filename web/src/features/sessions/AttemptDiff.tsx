import { useState } from "react";
import { ChevronDown } from "lucide-react";
import { useAttemptDiff } from "@/api/hooks";
import type { Candidate } from "@/api/types";
import { Collapsible, CollapsibleContent, CollapsibleTrigger } from "@/components/ui/collapsible";
import { cn } from "@/lib/utils";

/** One file's part of a unified diff: from its "diff --git a/… b/<path>" header to the next file's. */
function part(diff: string, path: string): string {
  const blocks = diff.split(/^(?=diff --git )/m);
  return blocks.find((block) => block.split("\n", 1)[0].endsWith(" b/" + path)) ?? "";
}

const tone = (line: string) => line.startsWith("+") && !line.startsWith("+++") ? "text-emerald-700 dark:text-emerald-400"
  : line.startsWith("-") && !line.startsWith("---") ? "text-destructive" : line.startsWith("@@") ? "text-primary" : "text-muted-foreground";

/** D56: what an Attempt's candidate changed — its files with line counts, and the diff of the one opened. */
export function AttemptDiff({ runId, attemptId, candidate }: { runId: string; attemptId: string; candidate: Candidate }) {
  const [open, setOpen] = useState(false);
  const [file, setFile] = useState<string | null>(null);
  const answer = useAttemptDiff(runId, attemptId, open);
  return (
    <Collapsible open={open} onOpenChange={setOpen} className="mt-2">
      <CollapsibleTrigger className="group flex items-center gap-1.5 text-xs font-medium text-primary">
        代码改动（{candidate.files} 个文件 +{candidate.insertions} −{candidate.deletions}）
        <ChevronDown className="size-3.5 transition-transform group-data-[state=open]:rotate-180" aria-hidden />
      </CollapsibleTrigger>
      <CollapsibleContent className="mt-2 space-y-2">
        {answer.isPending && <p className="text-muted-foreground">正在读取改动…</p>}
        {answer.error && <p className="text-destructive">{answer.error.message}</p>}
        {answer.data && (
          <>
            {answer.data.truncated && <p className="text-muted-foreground">改动较多，只显示了前一部分（200 KB 或 4000 行以内）。</p>}
            <ul aria-label="改动的文件" className="space-y-0.5">
              {answer.data.files.map((item) => (
                <li key={item.path}>
                  <button type="button" onClick={() => setFile(file === item.path ? null : item.path)} aria-expanded={file === item.path}
                    className={cn("flex w-full items-center gap-2 rounded px-1.5 py-0.5 text-left hover:bg-accent",
                      file === item.path && "bg-accent")}>
                    <span className="min-w-0 flex-1 truncate font-mono">{item.path}</span>
                    <span className="shrink-0 text-muted-foreground">{item.binary ? "二进制文件" : `+${item.insertions} −${item.deletions}`}</span>
                  </button>
                </li>
              ))}
            </ul>
            {file && (
              <pre aria-label={file + " 的改动"} className="max-h-96 overflow-auto rounded-md border bg-background p-2 font-mono text-[11px] leading-5">
                {(part(answer.data.diff, file) || "（这个文件的改动不在显示的部分中）").split("\n").map((line, index) => (
                  <span key={index} className={cn("block whitespace-pre", tone(line))}>{line || " "}</span>
                ))}
              </pre>
            )}
          </>
        )}
      </CollapsibleContent>
    </Collapsible>
  );
}
