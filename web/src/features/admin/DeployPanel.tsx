import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Check, Copy, Plus } from "lucide-react";
import { api } from "@/api/client";
import { keys, useAwsRequests } from "@/api/hooks";
import type { AwsRequest } from "@/api/types";
import { FormDialog } from "@/components/FormDialog";
import { NativeSelect } from "@/components/NativeSelect";
import { Panel } from "@/components/Panel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { formatTime } from "@/lib/labels";
import { cn } from "@/lib/utils";

const KIND = { native: "给边界开公网原生入口", backup: "异地备份", up: "更新单机版的栈" } as const;
const STATE = { pending: ["待执行", "bg-info/10 text-info"], done: ["已完成", "bg-success/10 text-success"],
  failed: ["没有完成", "bg-destructive/10 text-destructive"] } as const;

/** What a change is about, in a few words: the boundary or the bucket. */
function about(request: AwsRequest) {
  return request.kind === "native" ? "边界 " + request.params.boundary : request.kind === "backup" ? "S3 桶 " + request.params.bucket : "";
}

/** What the kit reported having made. */
function outcome(request: AwsRequest) {
  const result = request.result ?? {};
  if (request.state === "failed") return String(result.error ?? "部署套件报告没有完成");
  if (request.kind === "native" && result.native_domain) return "原生入口：https://" + result.native_domain + "/";
  if (request.kind === "backup" && result.key) return `已上传 s3://${result.bucket}/${result.key}`;
  return "";
}

/** 管理 › 部署 (MVP 0.2.0 P2, design §4.2.4, S29): after the install the host role keeps Bedrock only, so changes to AWS
 * are the deployment kit's, run by an administrator off the host; this page keeps what is waiting and the command. */
export function DeployPanel() {
  const query = useAwsRequests(true);
  const [adding, setAdding] = useState(false);
  const [copied, setCopied] = useState("");
  const items = query.data?.items ?? [];
  return (
    <Panel title="部署"
      description="单机版装完后，主机角色只留 Bedrock 调用权限：给边界开公网原生入口、异地备份、更新栈这些要改 AWS 的事，由管理员在自己电脑或 CloudShell 上用部署套件 kirocrew-on single 执行。这里记下待执行的变更和要运行的命令；执行完，部署套件会回报。"
      actions={<Button type="button" onClick={() => setAdding(true)}><Plus />记录变更</Button>}>
      {query.error && <p className="text-sm text-destructive">读取失败：{query.error.message}</p>}
      {!query.isLoading && !items.length && !query.error && (
        <p className="text-sm text-muted-foreground">还没有待执行的 AWS 变更。需要时点「记录变更」，再按给出的命令运行部署套件。</p>
      )}
      {items.length > 0 && (
        <ul className="divide-y rounded-md border">
          {items.map((r) => {
            const [label, tone] = STATE[r.state];
            const result = outcome(r);
            return (
              <li key={r.id} className="space-y-2 p-4 text-sm">
                <div className="flex items-center gap-3">
                  <span className="font-medium">{KIND[r.kind]}</span>
                  <span className="text-xs text-muted-foreground">{about(r)}</span>
                  <span className={cn("rounded-sm px-1.5 py-0.5 text-xs font-medium", tone)}>{label}</span>
                  <span className="ml-auto text-xs text-muted-foreground tabular-nums">{formatTime(r.finished ?? r.created)}</span>
                </div>
                {r.state === "pending" && (
                  <div className="flex items-start gap-2 rounded-md border bg-muted/50 p-2">
                    <code className="min-w-0 flex-1 font-mono text-xs break-all">{r.command}</code>
                    <Button type="button" size="sm" variant={copied === r.id ? "secondary" : "outline"} aria-label={"复制命令：" + r.id}
                      onClick={() => navigator.clipboard?.writeText(r.command).then(() => setCopied(r.id))}>
                      {copied === r.id ? <Check /> : <Copy />}{copied === r.id ? "已复制" : "复制"}
                    </Button>
                  </div>
                )}
                {result && <p className={cn("text-xs", r.state === "failed" ? "text-destructive" : "text-muted-foreground")}>{result}</p>}
              </li>
            );
          })}
        </ul>
      )}
      {adding && <RecordDialog onClose={() => setAdding(false)} />}
    </Panel>
  );
}

function RecordDialog({ onClose }: { onClose: () => void }) {
  const client = useQueryClient();
  const [kind, setKind] = useState<AwsRequest["kind"]>("native");
  const [value, setValue] = useState("");
  const field = kind === "native" ? { key: "boundary", label: "边界", hint: "这台主机上要从公网打开的边界名称" }
    : kind === "backup" ? { key: "bucket", label: "S3 桶", hint: "放备份的桶（你自己的账号里）" } : null;
  return (
    <FormDialog title="记录变更" submitLabel="记录" onClose={onClose}
      description="记下一项要由部署套件执行的 AWS 变更；记录后这里给出要在你电脑或 CloudShell 上运行的命令。"
      onSubmit={async () => {
        await api("/admin/aws-requests", "POST", { kind, params: field ? { [field.key]: value.trim() } : {} });
        await client.invalidateQueries({ queryKey: keys.awsRequests });
        onClose();
        toast.success("已记录：" + KIND[kind]);
      }}>
      <div className="space-y-1.5">
        <Label htmlFor="aws-request-kind">变更</Label>
        <NativeSelect id="aws-request-kind" value={kind} onChange={(e) => { setKind(e.target.value as AwsRequest["kind"]); setValue(""); }}>
          {(Object.keys(KIND) as AwsRequest["kind"][]).map((k) => <option key={k} value={k}>{KIND[k]}</option>)}
        </NativeSelect>
      </div>
      {field && (
        <div className="space-y-1.5">
          <Label htmlFor="aws-request-value">{field.label}</Label>
          <Input id="aws-request-value" value={value} onChange={(e) => setValue(e.target.value)} required />
          <p className="text-xs text-muted-foreground">{field.hint}</p>
        </div>
      )}
    </FormDialog>
  );
}
