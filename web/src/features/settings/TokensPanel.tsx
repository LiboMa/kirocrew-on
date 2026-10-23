import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, MoreHorizontal, Plus } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { keys } from "@/api/hooks";
import type { ApiToken, IssuedToken } from "@/api/types";
import { FormDialog } from "@/components/FormDialog";
import { NativeSelect } from "@/components/NativeSelect";
import { Panel } from "@/components/Panel";
import { useRowMotion } from "@/components/rowMotion";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { expiresText } from "@/lib/tokens";
import { cn } from "@/lib/utils";

const SCOPES: Record<string, string[]> = { read: ["read"], execute: ["read", "execute"], manage: ["read", "execute", "manage"] };
/** D62: how long a new token lasts. */
const LIFETIMES = [["7d", "7 天"], ["30d", "30 天"], ["365d", "365 天"], ["never", "永不过期"]] as const;

type Open = { kind: "new" } | { kind: "revoke"; token: ApiToken } | null;

/** 接入：API Token for Claude Code, Codex, Kiro CLI and other clients. The page holds the list; a new token is made, and
 * shown exactly once, in a dialog; revoking asks first (PRD 2026-10-23, 管理 / 我的设置 in dialogs). */
export function TokensPanel() {
  const client = useQueryClient();
  const tokens = useQuery({ queryKey: keys.tokens, queryFn: () => api<ApiToken[]>("/tokens") });
  const [dialog, setDialog] = useState<Open>(null);
  const motion = useRowMotion();
  const scopesOf = (token: ApiToken) => { try { return (JSON.parse(token.scopes) as string[]).join(" · "); } catch { return token.scopes; } };

  return (
    <Panel title="API 与 Coding Agent 接入"
      description="为 Claude Code、Codex、Kiro CLI 等客户端各创建一枚 Token。所有请求沿用当前用户的共享权限；撤销 Token 会关闭对应状态流。"
      actions={<Button type="button" onClick={() => setDialog({ kind: "new" })}><Plus />新建 Token</Button>}>
      <pre className="rounded-lg border bg-muted/40 p-3 font-mono text-[11px] leading-6 whitespace-pre-wrap">
        {`REST: ${window.location.origin}/v1\n事件: GET /v1/runs/{id}/events?stream=1\nMCP: python3 -m octopus mcp\n环境变量: OCTOPUS_URL, OCTOPUS_API_TOKEN\nOpenAPI: /v1/openapi.json`}
      </pre>
      <p className="text-xs text-muted-foreground">MCP 通过 API 以该 Token 的用户身份操作，不会自动继承工具当前 Chat 的原生 Session 身份。</p>
      <div className="space-y-2">
        {tokens.data?.length === 0 && (
          <p className="rounded-lg border border-dashed px-3 py-6 text-center text-sm text-muted-foreground">
            还没有 Token。点「新建 Token」，为第一个客户端创建一枚。
          </p>
        )}
        {(tokens.data ?? []).map((token) => {
          const motionProps = motion.props(token.id);
          return (
            <div key={token.id} data-token-row data-fresh={motionProps["data-fresh"]}
              className={cn("flex items-center justify-between gap-2 rounded-lg border bg-card px-3 py-2 text-sm", motionProps.className)}>
              <span className="min-w-0">{token.label}<small className="ml-2 text-xs text-muted-foreground">{scopesOf(token)} · {expiresText(token.expires)} ·{" "}
                {token.last_used ? "最近使用 " + new Date(token.last_used * 1000).toLocaleString() : "从未使用"}</small></span>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button type="button" size="icon-sm" variant="ghost" aria-label={"Token 操作：" + token.label} data-token={token.id}>
                    <MoreHorizontal />
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent align="end" className="w-36">
                  <DropdownMenuItem className="text-destructive" onSelect={() => setDialog({ kind: "revoke", token })}>撤销…</DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          );
        })}
      </div>
      {dialog?.kind === "new" && (
        <NewTokenDialog onClose={(made) => {
          setDialog(null);
          if (made) {
            motion.flash(made.id);
            toast.success("已创建 Token " + made.label);
          }
        }} />
      )}
      {dialog?.kind === "revoke" && (
        <FormDialog title="撤销 Token" destructive submitLabel="撤销 Token" onClose={() => setDialog(null)}
          description={<>撤销「{dialog.token.label}」后，使用它的客户端会立即失去访问，它打开的状态流也会关闭。这一步不能撤回。</>}
          onSubmit={async () => {
            const { id, label } = dialog.token;
            await api("/tokens/" + id, "DELETE", {});
            setDialog(null);
            await motion.leave(id, () => client.invalidateQueries({ queryKey: keys.tokens }));
            toast.success("已撤销 Token " + label);
          }} />
      )}
    </Panel>
  );
}

/** Make a token: its name, what it may do and how long it lasts; then, in the same dialog, the token itself, once. */
function NewTokenDialog({ onClose }: { onClose: (made?: { id: string; label: string }) => void }) {
  const client = useQueryClient();
  const [label, setLabel] = useState("");
  const [scope, setScope] = useState("execute");
  const [lifetime, setLifetime] = useState("30d");
  const [issued, setIssued] = useState<(IssuedToken & { label: string }) | null>(null);
  const [copied, setCopied] = useState(false);

  if (issued) {
    return (
      <FormDialog title="Token 已创建" closeLabel="我已保存" onClose={() => onClose({ id: issued.id, label: issued.label })}
        description="只显示这一次。把它配置到对应客户端的私有环境变量中。">
        <div className="flex items-center gap-2 rounded-lg border bg-muted/50 p-3 duration-300 animate-in fade-in-0 zoom-in-95 motion-reduce:animate-none">
          <code className="secret min-w-0 flex-1 font-mono text-xs break-all">{issued.token}</code>
          <Button type="button" size="sm" variant={copied ? "secondary" : "outline"}
            onClick={() => navigator.clipboard?.writeText(issued.token).then(() => { setCopied(true); toast.success("已复制"); })}>
            {copied ? <Check /> : <Copy />}{copied ? "已复制" : "复制"}
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">{expiresText(issued.expires)}，可随时撤销。</p>
      </FormDialog>
    );
  }

  return (
    <FormDialog title="新建 Token" submitLabel="创建 Token" onClose={() => onClose()}
      description="为一个客户端（Claude Code、Codex、Kiro CLI 等）创建一枚 Token。它的请求沿用你的权限；撤销后立即失效。"
      onSubmit={async () => {
        const name = label.trim() || "Coding client";
        const result = await api<IssuedToken>("/tokens", "POST", { label: name, scopes: SCOPES[scope], expires_in: lifetime });
        await client.invalidateQueries({ queryKey: keys.tokens });
        setIssued({ ...result, label: name });
      }}>
      <div className="space-y-1.5">
        <Label htmlFor="token-label">Token 名称</Label>
        <Input id="token-label" autoFocus value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Claude Code / Kiro CLI" maxLength={80} />
      </div>
      <div className="grid gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="token-scope">权限</Label>
          <NativeSelect id="token-scope" value={scope} onChange={(e) => setScope(e.target.value)}>
            <option value="read">只读状态</option><option value="execute">读取与提交开发</option><option value="manage">读取、执行与配置</option>
          </NativeSelect>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="token-lifetime">有效期</Label>
          <NativeSelect id="token-lifetime" value={lifetime} onChange={(e) => setLifetime(e.target.value)}>
            {LIFETIMES.map(([value, text]) => <option key={value} value={value}>{text}</option>)}
          </NativeSelect>
        </div>
      </div>
    </FormDialog>
  );
}
