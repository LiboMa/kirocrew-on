import { useState } from "react";
import { Pencil, Play, Plus, Share2 } from "lucide-react";
import type { Workflow } from "@/api/types";
import { ListCard, ListEmpty, RowActions } from "@/components/ListCard";
import { Panel } from "@/components/Panel";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { useConsole } from "@/features/console/context";
import { TOOLS } from "@/lib/labels";

/** 工作流 (owner 2026-10-24, 方案一): the workflow library as a page of the workbench beside the Sessions — it was a tab
 * of the Session list. A row's name opens the editor; 新建开发 starts a Session from it; its owner shares it. */
export function WorkflowsPage() {
  const { workflows, users, me, open } = useConsole();
  const [search, setSearch] = useState("");
  const term = search.trim().toLowerCase();
  const shown = term ? workflows.filter((f) => f.name.toLowerCase().includes(term)) : workflows;
  const whose = (flow: Workflow) => (flow.owner_id === me.id ? "我的" : users.find((u) => u.id === flow.owner_id)?.username ?? "已共享");
  const chain = (flow: Workflow) => flow.steps.map((s) => TOOLS[s.tool as keyof typeof TOOLS] ?? s.tool).join(" → ");

  return (
    <main id="console-main" className="min-h-0 min-w-0 flex-1 overflow-y-auto">
      <div className="mx-auto max-w-6xl space-y-10 px-4 py-6 sm:px-8 sm:py-8">
        <Panel title="工作流" description="可复用的任务链：每个任务用哪个工具、做什么。从一个工作流新建开发，或打开它修改；改动形成新的版本，已在跑的会话不受影响。"
          actions={<Button type="button" id="new-flow" onClick={() => open({ kind: "workflow", flow: null })}><Plus />新建工作流</Button>}>
          <ListCard label="工作流库" search={search} onSearch={setSearch} searchLabel="搜索工作流名称" summary={`共 ${workflows.length} 个`}>
            <div id="flow-list">
              {!workflows.length ? <ListEmpty>还没有工作流。点「新建工作流」，从模板构建第一个。</ListEmpty>
                : !shown.length ? <ListEmpty>没有名称含「{search.trim()}」的工作流。</ListEmpty> : (
                <Table>
                  <TableHeader>
                    <TableRow className="bg-muted/40 hover:bg-muted/40">
                      <TableHead className="pl-4">名称</TableHead><TableHead>任务</TableHead><TableHead>版本</TableHead>
                      <TableHead>归属</TableHead><TableHead className="pr-4 text-right">操作</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {shown.map((flow) => (
                      <TableRow key={flow.id}>
                        <TableCell className="pl-4">
                          <button type="button" data-flow={flow.id} onClick={() => open({ kind: "workflow", flow })}
                            className="rounded-sm text-left font-medium outline-none hover:text-primary focus-visible:ring-[3px] focus-visible:ring-ring/50">
                            {flow.name}
                          </button>
                          {!!flow.archived && <Badge variant="outline" className="ml-2 rounded-sm">已归档</Badge>}
                        </TableCell>
                        <TableCell className="max-w-md text-muted-foreground">
                          <span className="block truncate" title={chain(flow)}>{flow.steps.length} 个 · {chain(flow)}</span>
                        </TableCell>
                        <TableCell className="text-muted-foreground tabular-nums">v{flow.version}</TableCell>
                        <TableCell className="text-muted-foreground">{whose(flow)}</TableCell>
                        <TableCell className="pr-4 text-right">
                          <RowActions menu={"工作流操作：" + flow.name} actions={[
                            { icon: Play, label: "新建开发", name: "用它新建开发：" + flow.name, disabled: !!flow.archived,
                              onSelect: () => open({ kind: "launch", seed: { workflow_id: flow.id } }) },
                            { icon: Pencil, label: "编辑", name: "编辑工作流：" + flow.name, onSelect: () => open({ kind: "workflow", flow }) },
                          ]} more={flow.role === "owner" ? [
                            { icon: Share2, label: "共享", name: "共享工作流：" + flow.name, onSelect: () => open({ kind: "share", flowId: flow.id }) },
                          ] : []} />
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              )}
            </div>
          </ListCard>
        </Panel>
      </div>
    </main>
  );
}
