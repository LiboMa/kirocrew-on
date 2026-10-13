import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { api } from "@/api/client";
import { keys, useCredentials } from "@/api/hooks";
import type { Credential, CredentialKind, CredentialScope } from "@/api/types";
import { FormDialog } from "@/components/FormDialog";
import { NativeSelect } from "@/components/NativeSelect";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { useConsole } from "@/features/console/context";
import { elevated } from "@/lib/access";
import { KIND_LABELS, ownerPhrase } from "@/lib/credentials";

const DEFAULT_HOST: Record<CredentialKind, string> = { github_pat: "github.com", gitlab_token: "gitlab.com" };
const SECTIONS: { scope: CredentialScope; title: string }[] = [
  { scope: "user", title: "个人" }, { scope: "team", title: "Team" }, { scope: "platform", title: "平台" }];

const day = (seconds: number | null) => (seconds ? new Date(seconds * 1000).toLocaleDateString() : "");

/** One credential: metadata, and for those who manage it test / disable / rotate / delete (D35 §6). */
function Row({ credential }: { credential: Credential }) {
  const client = useQueryClient();
  const [result, setResult] = useState("");
  const [rotating, setRotating] = useState(false);
  const [secret, setSecret] = useState("");
  const [confirming, setConfirming] = useState(false);
  const refresh = () => client.invalidateQueries({ queryKey: keys.credentials });
  const run = (action: () => Promise<unknown>) => action().then(refresh).catch((error: Error) => toast.error(error.message));
  const path = "/credentials/" + encodeURIComponent(credential.id);

  return (
    <div data-credential={credential.id} className="space-y-2 rounded-lg border bg-card px-3 py-2 text-sm">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-medium">{credential.name}</span>
        <span className="text-xs text-muted-foreground">
          {ownerPhrase(credential.scope, credential.owner)} {KIND_LABELS[credential.kind]} · {credential.host} · {credential.hint}
        </span>
        {credential.disabled && <Badge variant="outline">已停用</Badge>}
      </div>
      <p className="text-xs text-muted-foreground">
        仓库：{credential.repos.length ? credential.repos.join("、") : "该主机全部"} · {credential.access === "write" ? "读写" : "只读"}
        {credential.expires ? ` · ${day(credential.expires)} 到期` : ""}
        {` · ${credential.last_used ? "最近使用 " + new Date(credential.last_used * 1000).toLocaleString() : "从未使用"}`}
      </p>
      {result && <p className="text-xs" role="status">{result}</p>}
      {credential.can_manage && (
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" size="sm" variant="outline" onClick={() =>
            api<{ ok: boolean; account?: string; expires?: number | null; error?: string }>(path + "/test", "POST", {})
              .then((answer) => {
                setResult(answer.ok ? `托管方确认：账号 ${answer.account}${answer.expires ? `，${day(answer.expires)} 到期` : ""}`
                  : answer.error ?? "托管方未确认");
                return refresh();
              }).catch((error: Error) => toast.error(error.message))}>测试</Button>
          <Button type="button" size="sm" variant="outline"
            onClick={() => run(() => api(path, "PATCH", { disabled: !credential.disabled }))}>
            {credential.disabled ? "启用" : "停用"}
          </Button>
          <Button type="button" size="sm" variant="outline" onClick={() => setRotating(!rotating)}>轮换</Button>
          <Button type="button" size="sm" variant="ghost" className="text-destructive"
            onClick={() => (confirming ? run(() => api(path, "DELETE", {})) : setConfirming(true))}>
            {confirming ? "确认删除" : "删除"}
          </Button>
        </div>
      )}
      {rotating && (
        <div className="flex items-center gap-2">
          <Label htmlFor={"rotate-" + credential.id} className="sr-only">新的 Token</Label>
          <Input id={"rotate-" + credential.id} type="password" autoComplete="off" value={secret} placeholder="新的 Token"
            onChange={(event) => setSecret(event.target.value)} />
          <Button type="button" size="sm" disabled={!secret} onClick={() =>
            run(() => api(path, "PATCH", { secret })).then(() => { setSecret(""); setRotating(false); })}>保存</Button>
        </div>
      )}
    </div>
  );
}

/** Settings → 代码仓库凭证: personal, Team and platform credentials; the secret is entered here and never shown again. */
export function CredentialsDialog() {
  const { close, me, teams, allTeams } = useConsole();
  const client = useQueryClient();
  const { data: credentials = [] } = useCredentials();
  const admin = elevated(me);
  const owned = (admin ? allTeams : teams.filter((team) => team.role === "owner"));
  const [name, setName] = useState("");
  const [kind, setKind] = useState<CredentialKind>("github_pat");
  const [host, setHost] = useState(DEFAULT_HOST.github_pat);
  const [owner, setOwner] = useState("user");
  const [repos, setRepos] = useState("");
  const [access, setAccess] = useState<"read" | "write">("read");
  const [secret, setSecret] = useState("");

  return (
    <FormDialog title="代码仓库凭证" kicker="CREDENTIALS" wide onClose={close} submitLabel="保存凭证"
      description="GitHub / GitLab 的访问 Token，用于克隆私有仓库。个人凭证只有你能用；Team 凭证由 Team owner 管理、成员的 Team 会话使用；平台凭证由管理员授权。Token 保存后不再显示，也不会进入任何会话、日志或 Coding Agent 的对话。"
      onSubmit={async () => {
        const scope = owner === "user" || owner === "platform" ? owner : "team";
        await api("/credentials", "POST", {
          name, kind, host: host.trim(), scope, ...(scope === "team" ? { team_id: owner } : {}),
          repos: repos.split(/[\n,，]/).map((r) => r.trim()).filter(Boolean), access, secret,
        });
        setName(""); setRepos(""); setSecret("");
        await client.invalidateQueries({ queryKey: keys.credentials });
        toast.success("凭证已保存");
      }}>
      <div className="space-y-4">
        {SECTIONS.map(({ scope, title }) => {
          const rows = credentials.filter((c) => c.scope === scope);
          return rows.length ? (
            <section key={scope} className="space-y-2" aria-label={title + "凭证"}>
              <h3 className="text-xs font-semibold text-muted-foreground">{title}</h3>
              {rows.map((credential) => <Row key={credential.id} credential={credential} />)}
            </section>
          ) : null;
        })}
        {!credentials.length && <p className="text-sm text-muted-foreground">还没有凭证。私有仓库的会话需要一枚可用的凭证。</p>}
      </div>
      <div className="grid gap-3 border-t pt-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="credential-name">名称</Label>
          <Input id="credential-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="例如：我的 GitHub" />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="credential-kind">类型</Label>
          <NativeSelect id="credential-kind" value={kind} onChange={(e) => {
            const next = e.target.value as CredentialKind;
            setKind(next);
            if (host === DEFAULT_HOST[kind]) setHost(DEFAULT_HOST[next]);
          }}>
            <option value="github_pat">GitHub fine-grained Token</option>
            <option value="gitlab_token">GitLab Token（含自建实例）</option>
          </NativeSelect>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="credential-host">主机</Label>
          <Input id="credential-host" value={host} onChange={(e) => setHost(e.target.value)} placeholder="github.com / gitlab.example.com" />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="credential-owner">归属</Label>
          <NativeSelect id="credential-owner" value={owner} onChange={(e) => setOwner(e.target.value)}>
            <option value="user">个人（只有我能用）</option>
            {owned.map((team) => <option key={team.id} value={team.id}>Team「{team.name}」</option>)}
            {admin && <option value="platform">平台（由管理员授权）</option>}
          </NativeSelect>
        </div>
        <div className="space-y-1.5 sm:col-span-2">
          <Label htmlFor="credential-repos">仓库范围（可选）</Label>
          <Textarea id="credential-repos" rows={2} value={repos} onChange={(e) => setRepos(e.target.value)}
            placeholder="每行一个：owner/repo、owner/* 或 group/sub/*；留空表示该主机全部仓库" />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="credential-access">权限</Label>
          <NativeSelect id="credential-access" value={access} onChange={(e) => setAccess(e.target.value as "read" | "write")}>
            <option value="read">只读（克隆）</option>
            <option value="write">读写（后续用于发布）</option>
          </NativeSelect>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="credential-secret">Token</Label>
          <Input id="credential-secret" type="password" autoComplete="off" value={secret} onChange={(e) => setSecret(e.target.value)}
            placeholder="保存后不再显示" />
        </div>
      </div>
    </FormDialog>
  );
}
