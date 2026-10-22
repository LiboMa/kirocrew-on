import { type ReactNode, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/api/client";
import { keys, useQuotas } from "@/api/hooks";
import type { Quotas } from "@/api/types";
import { FormDialog } from "@/components/FormDialog";
import { NativeSelect } from "@/components/NativeSelect";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useConsole } from "@/features/console/context";

type Scope = "default" | "user" | "team";

const usage = (used: number, limit: number | null) => `运行中 ${used} / ${limit == null ? "不限" : "上限 " + limit}`;

function useSave() {
  const client = useQueryClient();
  return async (scope: Scope, id: string | undefined, running: number | null) => {
    try {
      const answer = await api<Quotas>("/quotas", "POST", { scope, ...(id ? { id } : {}), running });
      client.setQueryData(keys.quotas, answer);
      toast.success(running == null ? "已取消上限" : "上限已保存");
    } catch (error) {
      toast.error((error as Error).message);
    }
  };
}

/** One limit someone may change: a number 1–50, saved; or removed (no limit). */
function Editor({ label, scope, id, value }: { label: string; scope: Scope; id?: string; value: number | null }) {
  const save = useSave();
  const [text, setText] = useState(value == null ? "" : String(value));
  const running = Number(text);
  const valid = Number.isInteger(running) && running >= 1 && running <= 50;
  return (
    <div className="flex items-center gap-2">
      <Input type="number" min={1} max={50} className="h-8 w-20" aria-label={`${label}的上限`} value={text}
        onChange={(e) => setText(e.target.value)} />
      <Button type="button" size="sm" variant="outline" disabled={!valid} onClick={() => save(scope, id, running)}>保存</Button>
      {value != null && (
        <Button type="button" size="sm" variant="ghost" onClick={() => { setText(""); void save(scope, id, null); }}>取消上限</Button>
      )}
    </div>
  );
}

function Row({ quota, label, detail, editor }: { quota: string; label: string; detail: string; editor?: ReactNode }) {
  return (
    <div data-quota={quota} className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border bg-card px-3 py-2 text-sm">
      <span className="min-w-0 flex-1"><span className="font-medium">{label}</span>
        <small className="ml-2 text-xs text-muted-foreground">{detail}</small></span>
      {editor}
    </div>
  );
}

/** Settings → 并发上限 (D61): how many tasks one runs at once, one's Teams too; administrators and Team owners set them. */
export function QuotasDialog() {
  const { close, users } = useConsole();
  const { data } = useQuotas();
  const save = useSave();
  const [person, setPerson] = useState("");
  const [limit, setLimit] = useState("2");
  const admin = data?.default !== undefined;
  const limited = new Set((data?.users ?? []).map((u) => u.id));

  return (
    <FormDialog title="并发上限" kicker="QUOTAS" wide onClose={close}
      description="同时运行的任务数上限。到了上限，会话的下一个任务按排队先后等待，有任务结束后自动开始，不必重新提交。没有设的范围不限制。每人默认与个人的上限由平台管理员设置，Team 的上限由 Team owner 或平台管理员设置。">
      {!data ? <p className="text-sm text-muted-foreground">读取中…</p> : (
        <div className="space-y-4">
          <section className="space-y-2" aria-label="我和我的 Team">
            <Row quota="mine" label="我" detail={usage(data.mine.used, data.mine.limit)} />
            {data.teams.map((team) => (
              <Row key={team.id} quota={`team:${team.id}`} label={`Team「${team.name}」`} detail={usage(team.used, team.limit)}
                editor={team.manage ? <Editor label={`Team「${team.name}」`} scope="team" id={team.id} value={team.limit} /> : undefined} />
            ))}
          </section>
          {admin && (
            <section className="space-y-2 border-t pt-4" aria-label="每人的上限">
              <Row quota="default" label="每人默认" detail={data.default == null ? "不限" : `上限 ${data.default}`}
                editor={<Editor label="每人默认" scope="default" value={data.default ?? null} />} />
              {(data.users ?? []).map((user) => (
                <Row key={user.id} quota={`user:${user.id}`} label={user.name} detail={`上限 ${user.limit}（单独设置，优先于默认）`}
                  editor={<Editor label={user.name} scope="user" id={user.id} value={user.limit} />} />
              ))}
              <div className="flex flex-wrap items-center gap-2">
                <NativeSelect aria-label="为某人单独设上限" className="h-8 w-48" value={person} onChange={(e) => setPerson(e.target.value)}>
                  <option value="">选择用户…</option>
                  {users.filter((u) => !limited.has(u.id)).map((u) => <option key={u.id} value={u.id}>{u.username}</option>)}
                </NativeSelect>
                <Input type="number" min={1} max={50} className="h-8 w-20" aria-label="单独的上限" value={limit}
                  onChange={(e) => setLimit(e.target.value)} />
                <Button type="button" size="sm" variant="outline" disabled={!person}
                  onClick={() => save("user", person, Number(limit)).then(() => setPerson(""))}>单独设置</Button>
              </div>
            </section>
          )}
        </div>
      )}
    </FormDialog>
  );
}
