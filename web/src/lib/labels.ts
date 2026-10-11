import type { Tool } from "@/api/types";

export const TOOLS: Record<Tool, string> = { kiro: "Kiro CLI", claude: "Claude Code", codex: "Codex", opencode: "OpenCode" };
export const EFFORTS = ["low", "medium", "high", "xhigh", "max"] as const;

export const STATES: Record<string, string> = {
  pending: "待执行", preparing: "准备源码", ready: "准备派发", dispatching: "派发中", running: "执行中",
  accepted: "已交接", completed: "已完成", blocked: "流程已暂停", failed: "执行失败", diagnosing: "协调者诊断中",
  waiting_worker: "待分配 Worker", waiting_approval: "等待原生权限", dispatch_unknown: "派发待核实",
  stopping: "停止中", stopped: "已停止", cancelled: "已取消", reopening: "重新打开中",
};

export const ACTIVE_STATES = new Set([
  "preparing", "ready", "dispatching", "running", "waiting_approval", "dispatch_unknown", "stopping", "reopening", "diagnosing",
]);

export const ATTENTION_STATES = new Set(["blocked", "failed", "waiting_worker", "waiting_approval", "dispatch_unknown"]);

export type Tone = "neutral" | "active" | "success" | "warning" | "danger";

export function stateTone(status: string): Tone {
  if (status === "accepted" || status === "completed") return "success";
  if (status === "blocked" || status === "failed") return "danger";
  if (status === "waiting_worker" || status === "waiting_approval" || status === "dispatch_unknown" || status === "stopped") return "warning";
  if (ACTIVE_STATES.has(status)) return "active";
  return "neutral";
}

export const EVENT_LABELS: Record<string, string> = {
  "run.created": "需求已登记", "attempt.created": "创建新的执行 Attempt", "attempt.state": "原生任务状态更新",
  "run.state": "开发状态更新", "run.completed": "全部任务已交接", "command.continue": "已提交原地续作",
  "command.recover": "收到诊断请求", "recovery.started": "Coordinator 开始诊断", "recovery.applied": "已安排返工与复审",
  "recovery.blocked": "协调者等待补充条件", "command.stop": "收到停止请求", "command.close": "收到关闭请求",
  "command.delete": "收到删除请求", "command.reopen": "收到重新打开请求", "session.stop": "原生任务已停止",
  "session.close": "已备份并关闭", "session.delete": "原生会话已删除，源码保留",
};

export const RECOVERY_STATES: Record<string, string> = {
  pending: "等待协调者", running: "诊断中", waiting_approval: "诊断等待权限", dispatch_unknown: "派发待核实",
  applied: "已安排修复", blocked: "需要补充条件", cancelled: "已取消",
};

export function formatTime(seconds: number, withDate = false): string {
  const date = new Date(seconds * 1000);
  return withDate
    ? date.toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })
    : date.toLocaleTimeString("zh-CN", { hour12: false });
}
