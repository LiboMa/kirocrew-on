import type { AdmissionBasis, Policy, RequestClient } from "@/api/types";
import { ROLE_LABELS } from "@/lib/labels";

/** D55: the client a Request came from, as people know it. */
export const CLIENT_LABELS: Record<RequestClient, string> = {
  console: "控制台", claude: "Claude Code", kiro: "Kiro CLI", codex: "Codex", "kiro-ide": "Kiro IDE", api: "API",
};

/** Task kinds a mapping preference may name (octopus/domain.py KINDS). */
export const TASK_KIND_LABELS: Record<string, string> = {
  implement: "实现", review: "审查", test: "测试", deliver: "交付", design: "设计",
};

export const TOOLS = ["kiro", "claude", "codex", "opencode"] as const;

const MODE_TEXT = { auto: "自动开始", confirm: "等待确认" } as const;

/** Why a Session started at once or waits for its plan's confirmation; nothing when the submission said so itself. */
export function basisText(basis: AdmissionBasis | null | undefined): string {
  if (!basis || basis.basis === "submitted") return "";
  if (basis.basis === "rule") return `按准入规则「${basis.pattern ?? ""}」${MODE_TEXT[basis.mode]}`;
  return `按平台默认${MODE_TEXT[basis.mode]}`;
}

export function clientLabel(client: string | null | undefined): string {
  return client ? CLIENT_LABELS[client as RequestClient] ?? client : "";
}

/** One rule as a line: 「仓库 github.com/o/* · 自动开始」 or 「角色 Evaluator → codex（模型 gpt-5）」. */
export function policyText(policy: Policy): string {
  const { rule } = policy;
  if (policy.kind === "admission") return `仓库 ${rule.repos} · ${MODE_TEXT[rule.mode ?? "confirm"]}`;
  const target = rule.role ? `角色 ${ROLE_LABELS[rule.role] ?? rule.role}` : `类别 ${TASK_KIND_LABELS[rule.kind ?? ""] ?? rule.kind}`;
  return `${target} → ${rule.tool}${rule.model ? `（模型 ${rule.model}）` : ""}`;
}
