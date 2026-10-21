import type { Tool } from "@/api/types";

export const TOOLS: Record<Tool, string> = { kiro: "Kiro CLI", claude: "Claude Code", codex: "Codex", opencode: "OpenCode" };
export const EFFORTS = ["low", "medium", "high", "xhigh", "max"] as const;

export const STATES: Record<string, string> = {
  pending: "待执行", preparing: "准备源码", ready: "准备派发", dispatching: "派发中", running: "执行中",
  accepted: "已交接", completed: "已完成", blocked: "流程已暂停", failed: "执行失败", diagnosing: "协调者诊断中",
  waiting_worker: "待分配 Worker", waiting_approval: "等待原生权限", dispatch_unknown: "派发待核实",
  stopping: "停止中", stopped: "已停止", cancelled: "已取消", reopening: "重新打开中", awaiting_confirmation: "计划待确认", awaiting_acceptance: "验收待确认",
};

export const ACTIVE_STATES = new Set([
  "preparing", "ready", "dispatching", "running", "waiting_approval", "dispatch_unknown", "stopping", "reopening", "diagnosing",
]);

export const ATTENTION_STATES = new Set(["blocked", "failed", "waiting_worker", "waiting_approval", "dispatch_unknown",
  "awaiting_confirmation", "awaiting_acceptance"]);

export type Tone = "neutral" | "active" | "success" | "warning" | "danger";

export function stateTone(status: string): Tone {
  if (status === "accepted" || status === "completed") return "success";
  if (status === "blocked" || status === "failed") return "danger";
  if (status === "waiting_worker" || status === "waiting_approval" || status === "dispatch_unknown" || status === "stopped" ||
      status === "awaiting_confirmation" || status === "awaiting_acceptance") return "warning";
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
  "command.assign-worker": "已分配 Worker", "run.binding": "原生会话与工作区已就绪", "attempt.dispatched": "已派发原生任务",
  "attempt.running": "原生任务执行中", "attempt.retry": "瞬时失败，自动重试", "task.reset": "任务重置为待执行",
  "task.spec": "任务配置已修改", "task.state": "任务状态更新", "run.settings": "会话设置已修改",
  "recovery.state": "协调者记录更新", "plan.applied": "KiroCrew 规划已应用", "run.resumed": "条件满足，自动继续",
  "run.awaiting_confirmation": "计划待确认", "command.confirm": "收到确认请求", "run.confirmed": "已确认计划",
  "run.publish_auto": "自动发布", "run.publish_policy": "发布设置已修改", "run.published": "已发布到仓库", "run.repository": "已新建仓库", "run.repository_used": "发布到已有的空仓库",
  "run.awaiting_acceptance": "验收待确认", "run.accepted": "已确认验收", "command.accept": "收到验收确认", "run.evaluator": "平台追加 Evaluator",
  "task.steered": "已补充说明", "run.knowledge_task": "平台追加 Knowledge 沉淀", "run.knowledge": "沉淀项目知识", "run.knowledge_forgotten": "已删除沉淀的知识", "run.knowledge_proposed": "提出仓库知识",
  "run.credentials": "凭证已更换", "run.downloaded": "已下载源码", "run.planning": "需求已改，原规划已过期",
  "run.budget": "等待时间不计入预算", "session.reopen": "原生会话已重新打开", "app.started": "远程预览已启动",
  "app.stopped": "远程预览已停止", "approval.answered": "已答复工具审批", "attempt.checking": "正在运行检查命令",
  "attempt.waiting": "等待条件满足", "attempt.watchdog": "无进展检查", "attempt.budget": "预算用完，未自动处理",
  "task.replaced": "重新规划替换了后续任务", "task.tool_switched": "已切换执行工具", "task.verify_revised": "重新规划修正了检查命令",
};

/** D46 P2: the roles a task's Agent works in (octopus/roles.py LABELS; kept equal by tests/test_roles.py). */
export const ROLE_LABELS: Record<string, string> = {
  spec: "Spec 分析", sde: "SDE 开发", integration_test: "集成测试", security_test: "安全测试", challenge: "Challenge", evaluator: "Evaluator",
  knowledge: "Knowledge",
};

/** Change-log entries that repeat an Attempt or recovery event; shown in 「变更记录」, not in the timeline. */
export const TIMELINE_HIDDEN = new Set(["task.state", "recovery.state"]);

const ADMIN_VERBS: Record<string, string> = {
  confirm: "确认计划", accept: "确认验收", stop: "停止", close: "关闭", delete: "删除", reopen: "重新打开", continue: "续作", recover: "请求诊断", "assign-worker": "分配 Worker",
};

/** An event's title; an administrator acting on someone else's Session is named (spec §3.4). */
export function eventLabel(event: { kind: string; payload: Record<string, unknown> }): string {
  const by = event.payload.by as { username?: string } | undefined;
  const verb = ADMIN_VERBS[event.kind.replace(/^command\./, "")];
  if (event.payload.oversight && by?.username && verb) return `由管理员 ${by.username} ${verb}`;
  return EVENT_LABELS[event.kind] ?? event.kind;
}

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
