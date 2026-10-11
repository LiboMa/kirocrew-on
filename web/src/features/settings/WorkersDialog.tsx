import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/api/client";
import { keys } from "@/api/hooks";
import { FormDialog } from "@/components/FormDialog";
import { NativeSelect } from "@/components/NativeSelect";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useConsole } from "@/features/console/context";

export function WorkersDialog() {
  const { workers, teams, users, me, close } = useConsole();
  const client = useQueryClient();
  const [form, setForm] = useState({ name: "", owner: "user:" + me.id, url: "", token: "" });
  const refresh = () => client.invalidateQueries({ queryKey: keys.workers });
  const act = (action: () => Promise<unknown>, done: string) => () =>
    action().then(refresh).then(() => toast.success(done)).catch((e: Error) => toast.error(e.message));
  const owner = (w: (typeof workers)[number]) => teams.find((t) => t.id === w.team_id)?.name ?? users.find((u) => u.id === w.owner_id)?.username ?? "已分配空间";

  return (
    <FormDialog title="执行环境 · KiroCrew Worker" kicker="WORKERS" onClose={close} submitLabel="登记 Worker"
      description="每个 Worker 对应一个 KiroCrew Gateway。isolation 字段是部署声明，不能替代实际操作系统隔离。"
      onSubmit={me.admin ? async () => {
        const [kind, id] = form.owner.split(":");
        await api("/workers", "POST", { name: form.name, [kind === "team" ? "team_id" : "owner_id"]: id, connection: { url: form.url, token: form.token } });
        await refresh();
        setForm({ ...form, name: "", url: "", token: "" });
        toast.success("Worker 已登记。");
      } : undefined}>
      <div className="space-y-2">
        {workers.map((w) => (
          <div key={w.id} className="flex flex-wrap items-center gap-2 rounded-lg border bg-card px-3 py-2 text-sm">
            <span className="min-w-0 flex-1">{w.name}
              <small className="ml-2 text-xs text-muted-foreground">
                {w.capabilities.isolation === "dedicated" ? "专属环境" : "个人本机"} · {w.enabled ? "可用" : "已停用"} · {owner(w)}
              </small>
            </span>
            <Button type="button" size="sm" variant="outline" data-refresh-worker={w.id}
              onClick={act(() => api("/workers/" + w.id + "/capabilities"), "已重新读取 Worker 模型目录。")}>刷新模型</Button>
            {!!me.admin && (
              <Button type="button" size="sm" variant="outline" data-toggle-worker={w.id}
                onClick={act(() => api("/workers/" + w.id + "/enabled", "POST", { enabled: !w.enabled }), w.enabled ? "已停用。" : "已启用。")}>
                {w.enabled ? "停用" : "启用"}
              </Button>
            )}
          </div>
        ))}
        {!workers.length && <p className="text-sm text-muted-foreground">还没有可见的 Worker。</p>}
      </div>
      {me.admin ? (
        <section className="space-y-3 border-t pt-4">
          <h3 className="text-sm font-semibold">登记已启动的 Worker</h3>
          <p className="text-xs text-muted-foreground">Worker 连接凭据由管理员填写，普通用户不会获得。Gateway 与 Coding 工具的认证保留在 Worker 内。</p>
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="worker-name">名称</Label>
              <Input id="worker-name" required value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} placeholder="team-a-worker" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="worker-owner">绑定空间</Label>
              <NativeSelect id="worker-owner" value={form.owner} onChange={(e) => setForm({ ...form, owner: e.target.value })}>
                {users.map((u) => <option key={u.id} value={"user:" + u.id}>个人 · {u.username}</option>)}
                {teams.map((t) => <option key={t.id} value={"team:" + t.id}>Team · {t.name}</option>)}
              </NativeSelect>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="worker-url">Worker URL</Label>
              <Input id="worker-url" required value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} placeholder="http://127.0.0.1:8928" />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="worker-token">Worker Token</Label>
              <Input id="worker-token" type="password" required minLength={32} autoComplete="off" value={form.token}
                onChange={(e) => setForm({ ...form, token: e.target.value })} />
            </div>
          </div>
        </section>
      ) : <p className="text-sm text-muted-foreground">执行环境由管理员登记和分配。</p>}
    </FormDialog>
  );
}
