import { describe, expect, test } from "vitest";
import { eventLabel } from "@/lib/labels";

describe("event labels", () => {
  test("a Session that continued on its own says so (D43)", () => {
    expect(eventLabel({ kind: "run.resumed", payload: { reason: "Kiro 登录已恢复，自动继续" } })).toBe("条件满足，自动继续");
  });
});
