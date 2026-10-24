import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Copy, Plus, RefreshCw, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { keys } from "@/api/hooks";
import type { ApiToken, IssuedToken } from "@/api/types";
import { FormDialog } from "@/components/FormDialog";
import { ListCard, ListEmpty, RowActions } from "@/components/ListCard";
import { NativeSelect } from "@/components/NativeSelect";
import { Panel } from "@/components/Panel";
import { useRowMotion } from "@/components/rowMotion";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { expiresText } from "@/lib/tokens";

const SCOPES: Record<string, string[]> = { read: ["read"], execute: ["read", "execute"], manage: ["read", "execute", "manage"] };
/** D62: how long a new token lasts. */
const LIFETIMES = [["7d", "7 天"], ["30d", "30 天"], ["365d", "365 天"], ["never", "永不过期"]] as const;

type Open = { kind: "new" } | { kind: "regenerate"; token: ApiToken } | { kind: "revoke"; token: ApiToken } | null;

const scopesOf = (token: ApiToken): string[] => { try { return JSON.parse(token.scopes) as string[]; } catch { return [token.scopes]; } };

/** 接入：API Token for Claude Code, Codex, Kiro CLI and other clients. The page holds the list; a new token is made, and
 * shown exactly once, in a dialog; one that exists is regenerated there too (owner 2026-10-24), and revoking asks first
 * (PRD 2026-10-23, 管理 / 我的设置 in dialogs). The token this login uses offers neither: 退出登录 ends it. */
export function TokensPanel() {
  const client = useQueryClient();
  const tokens = useQuery({ queryKey: keys.tokens, queryFn: () => api<ApiToken[]>("/tokens") });
  const [dialog, setDialog] = useState<Open>(null);
  const [search, setSearch] = useState("");
  const motion = useRowMotion();
  const all = tokens.data ?? [];
  const term = search.trim().toLowerCase();
  const shown = term ? all.filter((t) => t.label.toLowerCase().includes(term)) : all;

  return (
    <Panel title="API 与 Coding Agent 接入"
      description="为 Claude Code、Codex、Kiro CLI 等客户端各创建一枚 Token。所有请求沿用当前用户的共享权限；撤销 Token 会关闭对应状态流。"
      actions={<Button type="button" onClick={() => setDialog({ kind: "new" })}><Plus />新建 Token</Button>}>
      <ListCard label="Token 列表" search={search} onSearch={setSearch} searchLabel="搜索 Token 名称"
        onRefresh={() => void tokens.refetch()} refreshing={tokens.isFetching} summary={tokens.data ? `共 ${all.length} 枚` : undefined}>
        {tokens.data && !all.length ? <ListEmpty>还没有 Token。点「新建 Token」，为第一个客户端创建一枚。</ListEmpty>
          : tokens.data && !shown.length ? <ListEmpty>没有名称含「{search.trim()}」的 Token。</ListEmpty> : (
          <Table>
            <TableHeader>
              <TableRow className="bg-muted/40 hover:bg-muted/40">
                <TableHead className="pl-4">名称</TableHead><TableHead>权限</TableHead><TableHead>到期</TableHead>
                <TableHead>最近使用</TableHead><TableHead className="pr-4 text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {shown.map((token) => {
                const motionProps = motion.props(token.id);
                return (
                  <TableRow key={token.id} data-token-row data-fresh={motionProps["data-fresh"]} className={motionProps.className}>
                    <TableCell className="pl-4">
                      <span className="font-medium">{token.label}</span>
                      {token.current && <Badge variant="secondary" className="ml-2 rounded-sm">当前登录</Badge>}
                    </TableCell>
                    <TableCell>
                      <span className="flex gap-1">
                        {scopesOf(token).map((s) => <code key={s} className="rounded-sm border px-1.5 py-px font-mono text-[11px] text-muted-foreground">{s}</code>)}
                      </span>
                    </TableCell>
                    <TableCell className="text-muted-foreground tabular-nums">{token.expires == null ? "永不过期" : new Date(token.expires * 1000).toLocaleDateString()}</TableCell>
                    <TableCell className="text-muted-foreground tabular-nums">{token.last_used ? "最近使用 " + new Date(token.last_used * 1000).toLocaleString() : "从未使用"}</TableCell>
                    <TableCell className="pr-4 text-right">
                      {token.current ? <span className="text-xs text-muted-foreground">退出登录即失效</span> : (
                        <RowActions menu={"Token 操作：" + token.label} actions={[
                          { icon: RefreshCw, label: "重新生成", name: "重新生成 Token：" + token.label, onSelect: () => setDialog({ kind: "regenerate", token }) },
                          { icon: Trash2, label: "撤销", name: "撤销 Token：" + token.label, tone: "danger", onSelect: () => setDialog({ kind: "revoke", token }) },
                        ]} />
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </ListCard>
      <div className="space-y-2">
        <h3 className="text-sm font-medium">接入方式</h3>
        <pre className="rounded-lg border bg-muted/40 p-3 font-mono text-[11px] leading-6 whitespace-pre-wrap">
          {`REST: ${window.location.origin}/v1\n事件: GET /v1/runs/{id}/events?stream=1\nMCP: python3 -m octopus mcp\n环境变量: OCTOPUS_URL, OCTOPUS_API_TOKEN\nOpenAPI: /v1/openapi.json`}
        </pre>
        <p className="text-xs text-muted-foreground">MCP 通过 API 以该 Token 的用户身份操作，不会自动继承工具当前 Chat 的原生 Session 身份。</p>
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
      {dialog?.kind === "regenerate" && (
        <RegenerateDialog token={dialog.token} onClose={(done) => {
          setDialog(null);
          if (done) {
            motion.flash(dialog.token.id);
            toast.success("已重新生成 Token " + dialog.token.label);
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

/** The value of a token just made or regenerated, shown this once with a copy button. */
function IssuedDialog({ title, issued, after, onClose }: { title: string; issued: IssuedToken; after: string; onClose: () => void }) {
  const [copied, setCopied] = useState(false);
  return (
    <FormDialog title={title} closeLabel="我已保存" onClose={onClose}
      description="只显示这一次。把它配置到对应客户端的私有环境变量中。">
      <div className="flex items-center gap-2 rounded-lg border bg-muted/50 p-3 duration-300 animate-in fade-in-0 zoom-in-95 motion-reduce:animate-none">
        <code className="secret min-w-0 flex-1 font-mono text-xs break-all">{issued.token}</code>
        <Button type="button" size="sm" variant={copied ? "secondary" : "outline"}
          onClick={() => navigator.clipboard?.writeText(issued.token).then(() => { setCopied(true); toast.success("已复制"); })}>
          {copied ? <Check /> : <Copy />}{copied ? "已复制" : "复制"}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">{expiresText(issued.expires)}，{after}</p>
    </FormDialog>
  );
}

/** Regenerate a token: the same entry — name and permissions — gets a new value; the old one is void at once. Its expiry
 * stays unless a new lifetime is chosen (one carrying admin: at most 30 days, as when it was made). */
function RegenerateDialog({ token, onClose }: { token: ApiToken; onClose: (done?: boolean) => void }) {
  const client = useQueryClient();
  const [lifetime, setLifetime] = useState("");
  const [issued, setIssued] = useState<IssuedToken | null>(null);
  const admin = scopesOf(token).includes("admin");

  if (issued) return <IssuedDialog title="Token 已重新生成" issued={issued} after="旧值已失效。" onClose={() => onClose(true)} />;

  return (
    <FormDialog title="重新生成 Token" submitLabel="重新生成" onClose={() => onClose()}
      description={<>为「{token.label}」生成新的值：名称和权限不变，旧值立即失效，正在用它的客户端要换成新值。</>}
      onSubmit={async () => {
        const result = await api<IssuedToken>(`/tokens/${token.id}/regenerate`, "POST", lifetime ? { expires_in: lifetime } : {});
        await client.invalidateQueries({ queryKey: keys.tokens });
        setIssued(result);
      }}>
      <div className="space-y-1.5">
        <Label htmlFor="regenerate-lifetime">有效期</Label>
        <NativeSelect id="regenerate-lifetime" value={lifetime} onChange={(e) => setLifetime(e.target.value)}>
          <option value="">保持原到期时间（{expiresText(token.expires)}）</option>
          {LIFETIMES.filter(([value]) => !admin || value === "7d" || value === "30d")
            .map(([value, text]) => <option key={value} value={value}>{value === "never" ? text : "从现在起 " + text}</option>)}
        </NativeSelect>
        {admin && <p className="text-xs text-muted-foreground">带 admin 权限的 Token 最长 30 天。</p>}
      </div>
    </FormDialog>
  );
}

/** Make a token: its name, what it may do and how long it lasts; then, in the same dialog, the token itself, once. */
function NewTokenDialog({ onClose }: { onClose: (made?: { id: string; label: string }) => void }) {
  const client = useQueryClient();
  const [label, setLabel] = useState("");
  const [scope, setScope] = useState("execute");
  const [lifetime, setLifetime] = useState("30d");
  const [issued, setIssued] = useState<(IssuedToken & { label: string }) | null>(null);

  if (issued) {
    return <IssuedDialog title="Token 已创建" issued={issued} after="可随时撤销。" onClose={() => onClose({ id: issued.id, label: issued.label })} />;
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
