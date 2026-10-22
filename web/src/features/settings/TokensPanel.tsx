import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { keys } from "@/api/hooks";
import type { ApiToken, IssuedToken } from "@/api/types";
import { NativeSelect } from "@/components/NativeSelect";
import { Panel } from "@/components/Panel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { expiresText } from "@/lib/tokens";

const SCOPES: Record<string, string[]> = { read: ["read"], execute: ["read", "execute"], manage: ["read", "execute", "manage"] };
/** D62: how long a new token lasts. */
const LIFETIMES = [["7d", "7 天"], ["30d", "30 天"], ["365d", "365 天"], ["never", "永不过期"]] as const;

/** 接入：API Token for Claude Code, Codex, Kiro CLI and other clients; a new one is shown exactly once. */
export function TokensPanel() {
  const client = useQueryClient();
  const tokens = useQuery({ queryKey: keys.tokens, queryFn: () => api<ApiToken[]>("/tokens") });
  const [label, setLabel] = useState("");
  const [scope, setScope] = useState("execute");
  const [lifetime, setLifetime] = useState("30d");
  const [issued, setIssued] = useState<IssuedToken | null>(null);
  const scopesOf = (token: ApiToken) => { try { return (JSON.parse(token.scopes) as string[]).join(" · "); } catch { return token.scopes; } };

  if (issued) {
    return (
      <Panel title="Token 已创建" actions={<Button type="button" variant="outline" onClick={() => setIssued(null)}>完成</Button>}>
        <p className="text-sm text-muted-foreground">仅显示这一次。将它配置到对应客户端的私有环境变量中。</p>
        <div className="flex items-center gap-2 rounded-lg border bg-muted/50 p-3">
          <code className="secret min-w-0 flex-1 font-mono text-xs break-all">{issued.token}</code>
          <Button type="button" size="icon-sm" variant="ghost" aria-label="复制 Token"
            onClick={() => navigator.clipboard?.writeText(issued.token).then(() => toast.success("已复制"))}><Copy /></Button>
        </div>
        <p className="text-xs text-muted-foreground">{expiresText(issued.expires)}，可随时撤销。</p>
      </Panel>
    );
  }

  return (
    <Panel title="API 与 Coding Agent 接入" submitLabel="创建 API Token"
      description="为 Claude Code、Codex、Kiro CLI 等客户端各创建一枚 Token。所有请求沿用当前用户的共享权限；撤销 Token 会关闭对应状态流。"
      onSubmit={async () => {
        const result = await api<IssuedToken>("/tokens", "POST", { label: label || "Coding client", scopes: SCOPES[scope], expires_in: lifetime });
        await client.invalidateQueries({ queryKey: keys.tokens });
        setIssued(result);
      }}>
      <pre className="rounded-lg border bg-muted/40 p-3 font-mono text-[11px] leading-6 whitespace-pre-wrap">
        {`REST: ${window.location.origin}/v1\n事件: GET /v1/runs/{id}/events?stream=1\nMCP: python3 -m octopus mcp\n环境变量: OCTOPUS_URL, OCTOPUS_API_TOKEN\nOpenAPI: /v1/openapi.json`}
      </pre>
      <p className="text-xs text-muted-foreground">MCP 通过 API 以该 Token 的用户身份操作，不会自动继承工具当前 Chat 的原生 Session 身份。</p>
      <div className="space-y-2">
        {(tokens.data ?? []).map((token) => (
          <div key={token.id} className="flex items-center justify-between rounded-lg border bg-card px-3 py-2 text-sm">
            <span>{token.label}<small className="ml-2 text-xs text-muted-foreground">{scopesOf(token)} · {expiresText(token.expires)} ·{" "}
              {token.last_used ? "最近使用 " + new Date(token.last_used * 1000).toLocaleString() : "从未使用"}</small></span>
            <Button type="button" size="sm" variant="ghost" className="text-destructive" data-token={token.id}
              onClick={() => api("/tokens/" + token.id, "DELETE", {}).then(() => client.invalidateQueries({ queryKey: keys.tokens }))
                .catch((e: Error) => toast.error(e.message))}>撤销</Button>
          </div>
        ))}
      </div>
      <div className="grid gap-3 border-t pt-4 sm:grid-cols-3">
        <div className="space-y-1.5">
          <Label htmlFor="token-label">新 Token 名称</Label>
          <Input id="token-label" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="Claude Code / Kiro CLI" />
        </div>
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
    </Panel>
  );
}
