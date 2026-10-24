import { useState, type ReactNode } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Ban, FolderGit2, KeyRound, PlugZap, Plus, Power, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { keys, useCredentials } from "@/api/hooks";
import type { Credential, CredentialKind, CredentialScope } from "@/api/types";
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
import { Textarea } from "@/components/ui/textarea";
import { useConsole } from "@/features/console/context";
import { elevated } from "@/lib/access";
import { KIND_LABELS } from "@/lib/credentials";

const DEFAULT_HOST: Record<CredentialKind, string> = { github_pat: "github.com", gitlab_token: "gitlab.com" };
/** The list reads personal, then Team, then platform credentials, as the sections it replaced did. */
const SCOPE_ORDER: Record<CredentialScope, number> = { user: 0, team: 1, platform: 2 };

type Open = { kind: "new" } | { kind: "repos" | "secret" | "toggle" | "delete"; credential: Credential } | null;

const day = (seconds: number | null) => (seconds ? new Date(seconds * 1000).toLocaleDateString() : "");
/** Repository ranges as typed: one per line (or comma-separated); empty means every repository on the host. */
const parseRepos = (text: string) => text.split(/[\n,，]/).map((r) => r.trim()).filter(Boolean);
const REPOS_PLACEHOLDER = "每行一个：owner/repo、owner/* 或 group/sub/*；留空表示该主机全部仓库";
/** Whose it is, as the 归属 column says it: 「个人（bob）」「Team「平台组」」「平台」. */
const ownership = (c: Credential) =>
  c.scope === "user" ? `个人（${c.owner?.username ?? "某用户"}）` : c.scope === "team" ? `Team「${c.owner?.name ?? "未知"}」` : "平台";

/** 代码仓库凭证: personal, Team and platform credentials. The page holds the list; adding one, changing its repository
 * range or token, disabling and deleting it happen in dialogs (PRD 2026-10-23, 管理 / 我的设置 in dialogs). The secret is
 * typed into a dialog once and never shown again; those who only use a credential see it without actions (D35 §6). */
export function CredentialsPanel() {
  const client = useQueryClient();
  const credentials = useCredentials();
  const [dialog, setDialog] = useState<Open>(null);
  const [search, setSearch] = useState("");
  // What the host answered to 「测试」, by credential: shown in its row until the page is left.
  const [tested, setTested] = useState<Record<string, string>>({});
  const motion = useRowMotion();
  const refresh = () => client.invalidateQueries({ queryKey: keys.credentials });
  const all = [...(credentials.data ?? [])].sort((a, b) => SCOPE_ORDER[a.scope] - SCOPE_ORDER[b.scope]);
  const term = search.trim().toLowerCase();
  const shown = term ? all.filter((c) => (c.name + " " + c.host).toLowerCase().includes(term)) : all;
  const path = (c: Credential) => "/credentials/" + encodeURIComponent(c.id);
  /** Closes a dialog that changed ``c``: its row lights up, and a toast says what was done. */
  const changed = (c: Credential, words: string) => {
    setDialog(null);
    motion.flash(c.id);
    toast.success(words);
  };

  const test = (c: Credential) =>
    api<{ ok: boolean; account?: string; expires?: number | null; error?: string }>(path(c) + "/test", "POST", {})
      .then((answer) => {
        const text = answer.ok ? `托管方确认：账号 ${answer.account}${answer.expires ? `，${day(answer.expires)} 到期` : ""}`
          : answer.error ?? "托管方未确认";
        setTested((current) => ({ ...current, [c.id]: text }));
        return refresh();
      }).catch((error: Error) => toast.error(error.message));

  return (
    <Panel title="代码仓库凭证"
      description="GitHub / GitLab 的访问 Token，用于克隆私有仓库。个人凭证只有你能用；Team 凭证由 Team owner 管理、成员的 Team 会话使用；平台凭证由管理员授权。Token 保存后不再显示，也不会进入任何会话、日志或 Coding Agent 的对话。"
      actions={<Button type="button" onClick={() => setDialog({ kind: "new" })}><Plus />添加凭证</Button>}>
      <ListCard label="凭证列表" search={search} onSearch={setSearch} searchLabel="搜索凭证名称或主机"
        onRefresh={() => void credentials.refetch()} refreshing={credentials.isFetching}
        summary={credentials.data ? `共 ${all.length} 个` : undefined}>
        {credentials.data && !all.length ? <ListEmpty>还没有凭证。私有仓库的会话需要一枚可用的凭证，点「添加凭证」添加。</ListEmpty>
          : credentials.data && !shown.length ? <ListEmpty>没有名称或主机含「{search.trim()}」的凭证。</ListEmpty> : (
          <Table>
            <TableHeader>
              <TableRow className="bg-muted/40 hover:bg-muted/40">
                <TableHead className="pl-4">名称</TableHead><TableHead>类型</TableHead><TableHead>主机</TableHead>
                <TableHead>归属</TableHead><TableHead>仓库范围</TableHead><TableHead>最近使用</TableHead>
                <TableHead className="pr-4 text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {shown.map((c) => {
                const motionProps = motion.props(c.id);
                return (
                  <TableRow key={c.id} data-credential={c.id} data-fresh={motionProps["data-fresh"]} className={motionProps.className}>
                    <TableCell className="pl-4 align-top whitespace-normal">
                      <span className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <span className="font-medium">{c.name}</span>
                        <code className="font-mono text-[11px] text-muted-foreground">{c.hint}</code>
                        {c.disabled && <Badge variant="outline" className="rounded-sm">已停用</Badge>}
                      </span>
                      {tested[c.id] && <p role="status" className="mt-1 text-xs text-muted-foreground">{tested[c.id]}</p>}
                    </TableCell>
                    <TableCell className="align-top text-muted-foreground">
                      {KIND_LABELS[c.kind] ?? c.kind} · {c.access === "write" ? "读写" : "只读"}
                    </TableCell>
                    <TableCell className="align-top font-mono text-xs">{c.host}</TableCell>
                    <TableCell className="align-top">{ownership(c)}</TableCell>
                    <TableCell className="max-w-64 align-top whitespace-normal">
                      {c.repos.length ? (
                        <span className="flex flex-wrap gap-1">
                          {c.repos.map((r) => <code key={r} className="rounded-sm border px-1.5 py-px font-mono text-[11px] text-muted-foreground">{r}</code>)}
                        </span>
                      ) : <span className="text-muted-foreground">该主机全部</span>}
                    </TableCell>
                    <TableCell className="align-top text-muted-foreground tabular-nums">
                      {c.last_used ? new Date(c.last_used * 1000).toLocaleString() : "从未使用"}
                      {c.expires ? <span className="block text-xs">{day(c.expires)} 到期</span> : null}
                    </TableCell>
                    <TableCell className="pr-4 text-right align-top">
                      {!c.can_manage ? <span className="text-xs text-muted-foreground">只可使用</span> : (
                        <RowActions menu={"凭证操作：" + c.name} actions={[
                          { icon: FolderGit2, label: "仓库范围", name: "仓库范围：" + c.name, onSelect: () => setDialog({ kind: "repos", credential: c }) },
                          { icon: KeyRound, label: "更换 Token", name: "更换 Token：" + c.name, onSelect: () => setDialog({ kind: "secret", credential: c }) },
                        ]} more={[
                          { icon: PlugZap, label: "测试", name: "测试凭证：" + c.name, direct: true, onSelect: () => void test(c) },
                          { icon: c.disabled ? Power : Ban, label: c.disabled ? "启用" : "停用", name: (c.disabled ? "启用凭证：" : "停用凭证：") + c.name,
                            onSelect: () => setDialog({ kind: "toggle", credential: c }) },
                          { icon: Trash2, label: "删除", name: "删除凭证：" + c.name, tone: "danger", onSelect: () => setDialog({ kind: "delete", credential: c }) },
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
      {dialog?.kind === "new" && (
        <NewCredentialDialog onClose={(made) => {
          setDialog(null);
          if (made) {
            motion.flash(made.id);
            toast.success("已添加凭证 " + made.name);
          }
        }} />
      )}
      {dialog?.kind === "repos" && <ReposDialog credential={dialog.credential} onClose={() => setDialog(null)}
        onSaved={() => changed(dialog.credential, "已保存仓库范围：" + dialog.credential.name)} />}
      {dialog?.kind === "secret" && <SecretDialog credential={dialog.credential} onClose={() => setDialog(null)}
        onSaved={() => changed(dialog.credential, "已更换 Token：" + dialog.credential.name)} />}
      {dialog?.kind === "toggle" && <ToggleDialog credential={dialog.credential} onClose={() => setDialog(null)}
        onSaved={(verb) => changed(dialog.credential, `已${verb}凭证 ${dialog.credential.name}`)} />}
      {dialog?.kind === "delete" && (
        <FormDialog title="删除凭证" destructive submitLabel="删除凭证" onClose={() => setDialog(null)}
          description={<>删除「{dialog.credential.name}」后，它的 Token 随之销毁；绑定它的会话下次克隆或发布时会被拒绝，需要换一枚凭证。这一步不能撤回。</>}
          onSubmit={async () => {
            const { id, name } = dialog.credential;
            await api(path(dialog.credential), "DELETE", {});
            setDialog(null);
            await motion.leave(id, refresh);
            toast.success("已删除凭证 " + name);
          }} />
      )}
    </Panel>
  );
}

/** A small group of a dialog's fields under its heading (PRD §3.1: sections, not a multi-step wizard). */
function Fields({ title, children }: { title: string; children: ReactNode }) {
  return (
    <fieldset className="space-y-3">
      <legend className="mb-2 text-xs font-semibold text-muted-foreground">{title}</legend>
      {children}
    </fieldset>
  );
}

/** Add a credential: what it is and whose, which repositories it reaches with what access, and its token — written once. */
function NewCredentialDialog({ onClose }: { onClose: (made?: { id: string; name: string }) => void }) {
  const { me, teams, allTeams } = useConsole();
  const client = useQueryClient();
  const admin = elevated(me);
  const owned = admin ? allTeams : teams.filter((team) => team.role === "owner");
  const [name, setName] = useState("");
  const [kind, setKind] = useState<CredentialKind>("github_pat");
  const [host, setHost] = useState(DEFAULT_HOST.github_pat);
  const [owner, setOwner] = useState("user");
  const [repos, setRepos] = useState("");
  const [access, setAccess] = useState<"read" | "write">("read");
  const [secret, setSecret] = useState("");

  return (
    <FormDialog title="添加凭证" submitLabel="保存凭证" onClose={() => onClose()}
      description="Token 保存后不再显示，也不会进入任何会话、日志或 Coding Agent 的对话。"
      onSubmit={async () => {
        const scope = owner === "user" || owner === "platform" ? owner : "team";
        const made = await api<Credential>("/credentials", "POST", {
          name, kind, host: host.trim(), scope, ...(scope === "team" ? { team_id: owner } : {}),
          repos: parseRepos(repos), access, secret,
        });
        await client.invalidateQueries({ queryKey: keys.credentials });
        onClose({ id: made.id, name: made.name });
      }}>
      <Fields title="是什么">
        <div className="grid gap-3 sm:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="credential-name">名称</Label>
            <Input id="credential-name" autoFocus value={name} onChange={(e) => setName(e.target.value)} placeholder="例如：我的 GitHub" />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="credential-kind">类型</Label>
            <NativeSelect id="credential-kind" value={kind} onChange={(e) => {
              const next = e.target.value as CredentialKind;
              setKind(next);
              if (host === DEFAULT_HOST[kind]) setHost(DEFAULT_HOST[next]);   // follow the kind unless a host was typed
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
        </div>
      </Fields>
      <Fields title="能访问哪些仓库">
        <div className="space-y-1.5">
          <Label htmlFor="credential-repos">仓库范围（可选）</Label>
          <Textarea id="credential-repos" rows={2} value={repos} onChange={(e) => setRepos(e.target.value)} placeholder={REPOS_PLACEHOLDER} />
        </div>
        <div className="space-y-1.5 sm:max-w-[calc(50%-0.375rem)]">
          <Label htmlFor="credential-access">权限</Label>
          <NativeSelect id="credential-access" value={access} onChange={(e) => setAccess(e.target.value as "read" | "write")}>
            <option value="read">只读（克隆）</option>
            <option value="write">读写（后续用于发布）</option>
          </NativeSelect>
        </div>
      </Fields>
      <Fields title="Token">
        <div className="space-y-1.5">
          <Label htmlFor="credential-secret">Token</Label>
          <Input id="credential-secret" type="password" autoComplete="off" value={secret} onChange={(e) => setSecret(e.target.value)}
            placeholder="保存后不再显示" />
        </div>
      </Fields>
    </FormDialog>
  );
}

/** Change which repositories a credential reaches, starting from its current range; only ``repos`` is sent. A refused
 * range keeps the dialog open with what was typed. */
function ReposDialog({ credential, onClose, onSaved }: { credential: Credential; onClose: () => void; onSaved: () => void }) {
  const client = useQueryClient();
  const [repos, setRepos] = useState(credential.repos.join("\n"));
  return (
    <FormDialog title="修改仓库范围" submitLabel="保存范围" onClose={onClose}
      description={<>「{credential.name}」能访问 {credential.host} 上的哪些仓库。留空表示该主机全部仓库。</>}
      onSubmit={async () => {
        await api("/credentials/" + encodeURIComponent(credential.id), "PATCH", { repos: parseRepos(repos) });
        await client.invalidateQueries({ queryKey: keys.credentials });
        onSaved();
      }}>
      <div className="space-y-1.5">
        <Label htmlFor="credential-range">仓库范围</Label>
        <Textarea id="credential-range" autoFocus rows={4} value={repos} placeholder={REPOS_PLACEHOLDER}
          onChange={(event) => setRepos(event.target.value)} />
      </div>
    </FormDialog>
  );
}

/** Disable a credential — sessions can no longer clone or publish with it, which the dialog says first — or enable it again. */
function ToggleDialog({ credential, onClose, onSaved }: { credential: Credential; onClose: () => void; onSaved: (verb: string) => void }) {
  const client = useQueryClient();
  const verb = credential.disabled ? "启用" : "停用";
  return (
    <FormDialog title={verb + "凭证"} submitLabel={verb + "凭证"} destructive={!credential.disabled} onClose={onClose}
      description={credential.disabled ? <>启用后，「{credential.name}」又可以被会话选用，绑定它的会话也能继续克隆和发布。</>
        : <>停用后，会话不能再用它克隆或发布：绑定「{credential.name}」的会话下次用到时会被拒绝，新会话也选不到它。随时可以再启用。</>}
      onSubmit={async () => {
        await api("/credentials/" + encodeURIComponent(credential.id), "PATCH", { disabled: !credential.disabled });
        await client.invalidateQueries({ queryKey: keys.credentials });
        onSaved(verb);
      }} />
  );
}

/** Give a credential a new token (rotation): typed once, sent as ``secret``, never shown; an empty one is not sent. */
function SecretDialog({ credential, onClose, onSaved }: { credential: Credential; onClose: () => void; onSaved: () => void }) {
  const client = useQueryClient();
  const [secret, setSecret] = useState("");
  return (
    <FormDialog title="更换 Token" submitLabel="保存 Token" onClose={onClose}
      description={<>为「{credential.name}」换上新的 Token（现为 {credential.hint}），旧的随即不再使用。保存后不再显示。</>}
      onSubmit={async () => {
        if (!secret) throw new Error("请输入新的 Token");
        await api("/credentials/" + encodeURIComponent(credential.id), "PATCH", { secret });
        await client.invalidateQueries({ queryKey: keys.credentials });
        onSaved();
      }}>
      <div className="space-y-1.5">
        <Label htmlFor="credential-rotate">新的 Token</Label>
        <Input id="credential-rotate" type="password" autoComplete="off" autoFocus value={secret} placeholder="保存后不再显示"
          onChange={(event) => setSecret(event.target.value)} />
      </div>
    </FormDialog>
  );
}
