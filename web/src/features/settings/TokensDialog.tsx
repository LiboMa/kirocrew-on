import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { keys } from "@/api/hooks";
import type { ApiToken, IssuedToken } from "@/api/types";
import { FormDialog } from "@/components/FormDialog";
import { NativeSelect } from "@/components/NativeSelect";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useConsole } from "@/features/console/context";

const SCOPES: Record<string, string[]> = { read: ["read"], execute: ["read", "execute"], manage: ["read", "execute", "manage"] };

export function TokensDialog() {
  const { close } = useConsole();
  const client = useQueryClient();
  const tokens = useQuery({ queryKey: keys.tokens, queryFn: () => api<ApiToken[]>("/tokens") });
  const [label, setLabel] = useState("");
  const [scope, setScope] = useState("execute");
  const [issued, setIssued] = useState<IssuedToken | null>(null);
  const scopesOf = (token: ApiToken) => { try { return (JSON.parse(token.scopes) as string[]).join(" · "); } catch { return token.scopes; } };

  if (issued) {
    return (
      <FormDialog title="Token 已创建" kicker="API TOKEN" onClose={close}>
        <p className="text-sm text-muted-foreground">仅显示这一次。将它配置到对应客户端的私有环境变量中。</p>
        <div className="flex items-center gap-2 rounded-lg border bg-muted/50 p-3">
          <code className="secret min-w-0 flex-1 font-mono text-xs break-all">{issued.token}</code>
          <Button type="button" size="icon-sm" variant="ghost" aria-label="复制 Token"
            onClick={() => navigator.clipboard?.writeText(issued.token).then(() => toast.success("已复制"))}><Copy /></Button>
        </div>
        <p className="text-xs text-muted-foreground">7 天后到期，可随时撤销。</p>
      </FormDialog>
    );
  }

  return (
    <FormDialog title="API 与 Coding Agent 接入" kicker="API TOKENS" onClose={close} submitLabel="创建 API Token"
      description="为 Claude Code、Codex、Kiro CLI 等客户端各创建一枚 Token。所有请求沿用当前用户的共享权限；撤销 Token 会关闭对应状态流。"
      onSubmit={async () => {
        const result = await api<IssuedToken>("/tokens", "POST", { label: label || "Coding client", scopes: SCOPES[scope], ttl: 7 * 86400 });
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
            <span>{token.label}<small className="ml-2 text-xs text-muted-foreground">{scopesOf(token)} · {new Date(token.expires * 1000).toLocaleDateString()} 到期</small></span>
            <Button type="button" size="sm" variant="ghost" className="text-destructive" data-token={token.id}
              onClick={() => api("/tokens/" + token.id, "DELETE", {}).then(() => client.invalidateQueries({ queryKey: keys.tokens }))
                .catch((e: Error) => toast.error(e.message))}>撤销</Button>
          </div>
        ))}
      </div>
      <div className="grid gap-3 border-t pt-4 sm:grid-cols-2">
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
      </div>
    </FormDialog>
  );
}
