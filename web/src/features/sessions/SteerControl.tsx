import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/api/client";
import { keys } from "@/api/hooks";
import type { Run, RunTask } from "@/api/types";
import { Button } from "@/components/ui/button";
import { NativeSelect } from "@/components/NativeSelect";
import { Textarea } from "@/components/ui/textarea";

/** D46 P5b: a note for a running task (KiroCrew steer) — now, or after the turn it is in. */
export function SteerControl({ run, task }: { run: Run; task: RunTask }) {
  const client = useQueryClient();
  const [open, setOpen] = useState(false);
  const [message, setMessage] = useState("");
  const [mode, setMode] = useState<"interrupt" | "follow_up">("interrupt");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!["running", "waiting_approval"].includes(task.status) || run.permissions?.publish === false) return null;
  if (!open) {
    return <Button type="button" size="sm" variant="outline" onClick={() => setOpen(true)}>补充说明</Button>;
  }
  const send = async () => {
    if (busy || !message.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await api(`/runs/${encodeURIComponent(run.id)}/steer`, "POST", { task_id: task.id, message, mode });
      toast.success(mode === "interrupt" ? "补充说明已送达正在运行的任务" : "补充说明将在这一轮结束后送达");
      setOpen(false);
      setMessage("");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      await client.invalidateQueries({ queryKey: keys.run(run.id) });
    }
  };
  return (
    <div className="w-full space-y-2 rounded-lg border bg-background p-3">
      <Textarea aria-label="补充说明" value={message} maxLength={4000} rows={3} onChange={(e) => setMessage(e.target.value)}
        placeholder="告诉正在运行的任务要注意或调整什么" />
      <div className="flex flex-wrap items-center gap-2">
        <NativeSelect aria-label="送达方式" value={mode} onChange={(e) => setMode(e.target.value as "interrupt" | "follow_up")}>
          <option value="interrupt">现在告诉它</option>
          <option value="follow_up">这一轮结束后</option>
        </NativeSelect>
        <Button type="button" size="sm" disabled={busy || !message.trim()} onClick={send}>发送</Button>
        <Button type="button" size="sm" variant="outline" onClick={() => setOpen(false)}>取消</Button>
      </div>
      {error && <p className="text-xs text-destructive">{error}</p>}
    </div>
  );
}
