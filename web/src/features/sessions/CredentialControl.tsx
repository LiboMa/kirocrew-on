import { useState } from "react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { useCredentials } from "@/api/hooks";
import type { Run } from "@/api/types";
import { NativeSelect } from "@/components/NativeSelect";
import { Button } from "@/components/ui/button";
import { useConsole } from "@/features/console/context";
import { useRefreshRun } from "@/features/sessions/useSessionActions";
import { elevated } from "@/lib/access";
import { SCOPE_LABELS, httpsHost } from "@/lib/credentials";

/** Change or remove a Session's source credential (D35 §4): its creator, a Team owner or an admin, with a credential
 * usable for the Session's host; the platform checks the rest. */
export function CredentialControl({ run }: { run: Run }) {
  const { me, allTeams } = useConsole();
  const refresh = useRefreshRun();
  const [open, setOpen] = useState(false);
  const [choice, setChoice] = useState("");
  const { data = [] } = useCredentials(open);
  const host = run.source.type === "git" ? httpsHost(run.source.url ?? "") : "";
  const allowed = run.owner_id === me.id || elevated(me) || allTeams.some((t) => t.id === run.team_id && t.role === "owner");
  if (!host || !allowed) return null;
  const options = data.filter((c) => c.host === host && !c.disabled && (c.scope !== "team" || c.owner?.id === run.team_id));
  const save = (source: string | null) =>
    api("/runs/" + encodeURIComponent(run.id) + "/credentials", "POST", { source })
      .then(async () => {
        await refresh(run.id);
        setOpen(false);
        toast.success(source ? "已更换凭证；续作时按新凭证准备源码。" : "已撤下凭证。");
      })
      .catch((error: Error) => toast.error(error.message));

  if (!open) {
    return <Button type="button" size="xs" variant="link" className="h-auto px-1" onClick={() => setOpen(true)}>更换凭证</Button>;
  }
  return (
    <span className="ml-1 inline-flex flex-wrap items-center gap-1.5">
      <label htmlFor="run-credential" className="sr-only">会话凭证</label>
      <NativeSelect id="run-credential" value={choice} onChange={(e) => setChoice(e.target.value)} className="h-7 text-xs">
        <option value="">选择 {host} 的凭证</option>
        {options.map((c) => <option key={c.id} value={c.id}>{c.name} · {SCOPE_LABELS[c.scope]} · {c.hint}</option>)}
      </NativeSelect>
      <Button type="button" size="xs" disabled={!choice} onClick={() => save(choice)}>保存凭证</Button>
      {run.credentials?.source && <Button type="button" size="xs" variant="outline" onClick={() => save(null)}>撤下</Button>}
      <Button type="button" size="xs" variant="ghost" onClick={() => setOpen(false)}>取消</Button>
    </span>
  );
}
