import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { api } from "@/api/client";
import { keys } from "@/api/hooks";
import type { KnowledgeItem, Run } from "@/api/types";
import { Button } from "@/components/ui/button";

const RESULTS: Record<KnowledgeItem["result"], string> = {
  written: "已写入", superseded: "已写入（替代旧条目）", refused: "KiroCrew 未接受", skipped: "未写入", kept: "仅提到仓库（发布时）",
};

function Item({ run, item, canDelete }: { run: Run; item: KnowledgeItem; canDelete: boolean }) {
  const client = useQueryClient();
  const [confirm, setConfirm] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const stored = (item.result === "written" || item.result === "superseded") && !item.removed;
  const forget = async () => {
    if (busy) return;
    setBusy(true);
    setError(null);
    try {
      await api(`/runs/${encodeURIComponent(run.id)}/knowledge/forget`, "POST", { id: item.id });
      setConfirm(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
      await client.invalidateQueries({ queryKey: keys.run(run.id) });
    }
  };
  return (
    <li className="rounded-lg border bg-background p-3 text-sm">
      <p className={item.removed ? "text-muted-foreground line-through" : undefined}>{item.rule}</p>
      {item.avoid && <p className="mt-1 text-xs text-muted-foreground">不要：{item.avoid}</p>}
      <p className="mt-1 text-xs text-muted-foreground">
        {item.removed ? `已删除${item.removed_by ? `（${item.removed_by}）` : ""}`
          : [RESULTS[item.result] ?? item.result, item.reason].filter(Boolean).join("：")}
      </p>
      {error && <p className="mt-1 text-xs text-destructive">{error}</p>}
      {canDelete && stored && (confirm ? (
        <div className="mt-2 flex gap-2">
          <Button size="sm" variant="destructive" disabled={busy} onClick={forget}>确认删除</Button>
          <Button size="sm" variant="outline" onClick={() => setConfirm(false)}>取消</Button>
        </div>
      ) : (
        <Button className="mt-2" size="sm" variant="outline" onClick={() => setConfirm(true)}>删除</Button>
      ))}
    </li>
  );
}

/** D46 P4: what this Session wrote to its execution environment's KiroCrew lessons; the repository's later tasks get them. */
export function KnowledgePanel({ run }: { run: Run }) {
  const value = run.knowledge;
  if (!value) return null;
  const canDelete = run.permissions?.publish !== false;
  return (
    <section aria-label="本次沉淀的知识" className="space-y-2 rounded-xl border bg-card p-4">
      <h2 className="text-sm font-semibold">本次沉淀的知识</h2>
      {value.note ? <p className="text-xs text-muted-foreground">{value.note}</p> : !value.items.length ? (
        <p className="text-xs text-muted-foreground">Knowledge 任务完成后，在此列出写入执行环境的项目知识；同一仓库今后的任务会收到这些知识。</p>
      ) : <p className="text-xs text-muted-foreground">已写入的条目，同一仓库今后的任务会收到；删除后不再提供。</p>}
      {!!value.items.length && (
        <ul className="space-y-2">{value.items.map((item) => <Item key={item.id} run={run} item={item} canDelete={canDelete} />)}</ul>
      )}
      {!!value.items.length && !canDelete && <p className="text-xs text-muted-foreground">只有会话创建者、Team owner 或管理员可以删除。</p>}
    </section>
  );
}
