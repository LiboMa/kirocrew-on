import type { Policy, RequestClient } from "@/api/types";
import { ROLE_LABELS } from "@/lib/labels";

/** D55: the client a Request came from, as people know it. */
export const CLIENT_LABELS: Record<RequestClient, string> = {
  console: "控制台", cli: "CLI", claude: "Claude Code", kiro: "Kiro CLI", codex: "Codex", "kiro-ide": "Kiro IDE", api: "API",
};

/** Task kinds a mapping preference may name (octopus/domain.py KINDS). */
export const TASK_KIND_LABELS: Record<string, string> = {
  implement: "实现", review: "审查", test: "测试", deliver: "交付", design: "设计", research: "调研", write: "撰写",
};

export const TOOLS = ["kiro", "claude", "codex", "opencode"] as const;

export function clientLabel(client: string | null | undefined): string {
  return client ? CLIENT_LABELS[client as RequestClient] ?? client : "";
}

/** One rule as a line: 「角色 Evaluator → codex（模型 gpt-5）」; an admission rule from before D70 says it has no effect. */
export function policyText(policy: Policy): string {
  const { rule } = policy;
  if (policy.kind === "admission") return `准入规则 仓库 ${rule.repos}（已取消，不再生效，可删除）`;
  const target = rule.role ? `角色 ${ROLE_LABELS[rule.role] ?? rule.role}` : `类别 ${TASK_KIND_LABELS[rule.kind ?? ""] ?? rule.kind}`;
  return `${target} → ${rule.tool}${rule.model ? `（模型 ${rule.model}）` : ""}`;
}
