import { describe, expect, test } from "vitest";
import { usageLine } from "@/lib/usage";

describe("usage line (D46 P6a)", () => {
  test("says what was used, in the platform's words", () => {
    expect(usageLine({ credits: 12.4, seconds: 125.5, tokens: "unavailable" })).toBe("12.4 credits · 2 分 6 秒");
    expect(usageLine({ input_tokens: 120000, output_tokens: 8000, cost_usd: 0.84, seconds: 200 }))
      .toBe("输入 120k / 输出 8k token · $0.84 · 3 分 20 秒");
    expect(usageLine({ input_tokens: 1500, output_tokens: 2_300_000 })).toBe("输入 1.5k / 输出 2.3M token");
    expect(usageLine({ seconds: 42 })).toBe("42 秒");
    expect(usageLine({})).toBe("—");
  });
});
