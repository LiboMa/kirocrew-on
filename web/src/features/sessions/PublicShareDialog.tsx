import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/api/client";
import type { PublicShare, Run } from "@/api/types";
import { FormDialog } from "@/components/FormDialog";
import { NativeSelect } from "@/components/NativeSelect";
import { Button } from "@/components/ui/button";
import { Label } from "@/components/ui/label";
import { useConsole } from "@/features/console/context";

const KINDS = { pages: "页面", app: "运行中的演示" } as const;
const MODES = { code: "需要四位授权码", open: "无需授权码" } as const;
const EXPIRES = { "1d": "1 天", "7d": "7 天", "30d": "30 天", never: "永久" } as const;

const when = (seconds: number | null) => (seconds ? new Date(seconds * 1000).toLocaleString("zh-CN", { hour12: false }) : "永久");

/** Session → 公开分享 (D75): a link anyone on the Internet can open — with a four-digit code, or without. */
export function PublicShareDialog({ run }: { run: Run }) {
  const { close } = useConsole();
  const client = useQueryClient();
  const key = ["runs", run.id, "public-shares"];
  const base = `/runs/${encodeURIComponent(run.id)}/public-shares`;
  const listed = useQuery({ queryKey: key, queryFn: () => api<{ shares: PublicShare[]; available: boolean }>(base) });
  const [kind, setKind] = useState<keyof typeof KINDS>("pages");
  const [mode, setMode] = useState<keyof typeof MODES>("code");
  const [expires, setExpires] = useState<keyof typeof EXPIRES>("7d");
  const refresh = () => client.invalidateQueries({ queryKey: key });
  const copy = (text: string, what: string) =>
    navigator.clipboard?.writeText(text).then(() => toast.success(`已复制${what}`), () => toast.error("复制失败，请手动选择"));
  const act = (path: string, method: "POST" | "DELETE", done: string) =>
    api(base + path, method, method === "POST" ? {} : undefined).then(refresh).then(() => toast.success(done))
      .catch((error: Error) => toast.error(error.message));
  const shares = listed.data?.shares ?? [];
  const unavailable = listed.data?.available === false;

  return (
    <FormDialog title="公开分享" kicker="PUBLIC" wide onClose={close} submitLabel="生成链接"
      description="生成一条任何人都能打开的链接：互联网上的人不需要平台账号。需要授权码时，对方输入四位数字后才能看；连续输错会暂时锁定，累计输错 100 次授权码作废。页面分享只提供网页会加载的文件（HTML、样式、脚本、图片、字体等），不含源码、配置与数据库，按会话当前的产出提供；演示需要远程预览正在运行。随时可以撤销。"
      onSubmit={unavailable ? undefined : async () => {
        const made = await api<PublicShare>(base, "POST", { kind, mode, expires });
        await refresh();
        if (made.url) await navigator.clipboard?.writeText(made.code ? `${made.url}  授权码：${made.code}` : made.url).catch(() => {});
        toast.success(made.code ? `已生成链接，授权码 ${made.code}（已复制）` : "已生成链接（已复制）");
      }}>
      {unavailable && <p role="alert" className="text-sm text-destructive">平台尚未配置预览域名，不能公开分享：请平台管理员配置远程预览。</p>}
      <div className="grid gap-3 sm:grid-cols-3">
        <div className="space-y-1.5">
          <Label htmlFor="public-kind">分享内容</Label>
          <NativeSelect id="public-kind" value={kind} onChange={(e) => setKind(e.target.value as keyof typeof KINDS)}>
            {Object.entries(KINDS).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </NativeSelect>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="public-mode">访问方式</Label>
          <NativeSelect id="public-mode" value={mode} onChange={(e) => setMode(e.target.value as keyof typeof MODES)}>
            {Object.entries(MODES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </NativeSelect>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="public-expires">有效期</Label>
          <NativeSelect id="public-expires" value={expires} onChange={(e) => setExpires(e.target.value as keyof typeof EXPIRES)}>
            {Object.entries(EXPIRES).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
          </NativeSelect>
        </div>
      </div>
      {kind === "app" && <p className="text-xs text-muted-foreground">演示由你在会话页启动远程预览；访客不能启动它，空闲 30 分钟无人访问时自动停止。</p>}
      <section className="space-y-2 border-t pt-4" aria-label="已有的公开分享">
        {shares.length ? shares.map((share) => (
          <div key={share.id} data-public-share={share.id} className="space-y-2 rounded-lg border bg-card px-3 py-2.5 text-sm">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-medium">{KINDS[share.kind]}</span>
              <span className="text-xs text-muted-foreground">{MODES[share.mode]} · 到期 {when(share.expires)} · 打开 {share.views} 次</span>
              {share.locked_until && <span className="text-xs text-destructive">输错过多，已锁定至 {when(share.locked_until)}</span>}
              {share.code_retired && <span className="text-xs text-destructive">授权码多次输错，已作废：请重新生成</span>}
            </div>
            {share.url && (
              <div className="flex items-center gap-2">
                <code className="min-w-0 flex-1 truncate rounded bg-muted px-2 py-1 text-[12px]" title={share.url}>{share.url}</code>
                <Button type="button" size="sm" variant="outline" onClick={() => copy(share.url!, "链接")}>复制链接</Button>
              </div>
            )}
            <div className="flex flex-wrap items-center gap-2">
              {share.code && (
                <>
                  <span className="text-xs text-muted-foreground">授权码</span>
                  <code className="rounded bg-muted px-2 py-0.5 font-mono text-base tracking-[0.3em]" data-code>{share.code}</code>
                  <Button type="button" size="sm" variant="ghost" onClick={() => copy(share.code!, "授权码")}>复制</Button>
                  <Button type="button" size="sm" variant="ghost" onClick={() => act(`/${share.id}/code`, "POST", "已重新生成授权码，此前的授权失效")}>
                    重新生成授权码
                  </Button>
                </>
              )}
              <Button type="button" size="sm" variant="ghost" className="ml-auto text-destructive"
                onClick={() => act(`/${share.id}`, "DELETE", "已撤销，链接立即失效")}>撤销</Button>
            </div>
          </div>
        )) : <p className="text-sm text-muted-foreground">{listed.isPending ? "读取中…" : "还没有公开分享。"}</p>}
      </section>
    </FormDialog>
  );
}
