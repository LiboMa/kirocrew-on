import { describe, expect, test } from "vitest";
import { eventLabel } from "@/lib/labels";

describe("event labels", () => {
  test("a plan waiting for and given confirmation says so (D44)", () => {
    expect(eventLabel({ kind: "run.awaiting_confirmation", payload: {} })).toBe("计划待确认");
    expect(eventLabel({ kind: "command.confirm", payload: {} })).toBe("收到确认请求");
    expect(eventLabel({ kind: "run.confirmed", payload: { via: "continue" } })).toBe("已确认计划");
  });

  test("a Session that continued on its own says so (D43)", () => {
    expect(eventLabel({ kind: "run.resumed", payload: { reason: "Kiro 登录已恢复，自动继续" } })).toBe("条件满足，自动继续");
  });
});
