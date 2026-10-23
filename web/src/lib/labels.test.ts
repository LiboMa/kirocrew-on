import { describe, expect, test } from "vitest";
import { GATE_LABELS, ROLE_LABELS, eventLabel } from "@/lib/labels";

describe("event labels", () => {
  test("a plan waiting for and given confirmation says so (D44)", () => {
    expect(eventLabel({ kind: "run.awaiting_confirmation", payload: {} })).toBe("计划待确认");
    expect(eventLabel({ kind: "command.confirm", payload: {} })).toBe("收到确认请求");
    expect(eventLabel({ kind: "run.confirmed", payload: { via: "continue" } })).toBe("已确认计划");
  });

  test("a Session that continued on its own says so (D43)", () => {
    expect(eventLabel({ kind: "run.resumed", payload: { reason: "Kiro 登录已恢复，自动继续" } })).toBe("条件满足，自动继续");
  });
  test("a task's role has a title (D46 P2)", () => {
    expect(ROLE_LABELS.security_test).toBe("安全测试");
  });
  test("a diagnosis the platform retried says so (D57 §6.3)", () => {
    expect(eventLabel({ kind: "recovery.retry", payload: {} })).toBe("诊断遇到平台故障，自动重新诊断");
  });
});

describe("gate labels (Task 15)", () => {
  test("every gate has a Chinese name; the platform's name stays for the tooltip", () => {
    expect(GATE_LABELS).toEqual({
      native_completed: "原生执行", identity_match: "实际工具与模型", handoff_present: "交接文件", outcome_valid: "结果文件",
      outcome_pass: "任务结论", diff_present: "入库改动", no_secrets: "密钥检查", secrets_masked: "已打码的疑似密钥",
      artifacts_locked: "已验收的文件", checks_pass: "检查命令", tests_fail_on_baseline: "测试是否针对改动", review_bound: "审查对象",
      read_only: "知识沉淀只读", evaluated: "Evaluator",
    });
  });
});
