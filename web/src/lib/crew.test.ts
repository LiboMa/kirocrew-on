import { describe, expect, test } from "vitest";
import { validateCrewEntry } from "@/lib/crew";

const entry = (url: string, origin = new URL(url).origin, parent = "octopus-run-1") => ({ url, origin, native_parent: parent });

describe("native entry validation", () => {
  test("accepts the public native distribution for the selected Session", () => {
    const target = validateCrewEntry(entry("https://dnative.cloudfront.net/chat?sid=octopus-run-1&token=t"), "octopus-run-1", "d1.cloudfront.net");
    expect(target.hostname).toBe("dnative.cloudfront.net");
  });

  test("rejects a link for another Session, path or extra parameters", () => {
    const bad = [
      entry("https://n.example/chat?sid=other&token=t"),
      entry("https://n.example/admin?sid=octopus-run-1&token=t"),
      entry("https://n.example/chat?sid=octopus-run-1&token=t&next=/x"),
      entry("https://n.example/chat?sid=octopus-run-1&token=t", "https://evil.example"),
      entry("https://u:p@n.example/chat?sid=octopus-run-1&token=t"),
      entry("javascript:alert(1)//chat?sid=octopus-run-1&token=t", "null"),
    ];
    for (const value of bad) expect(() => validateCrewEntry(value, "octopus-run-1", "d1.cloudfront.net")).toThrow("不一致");
  });

  test("a loopback link is only followed from a loopback page", () => {
    const local = entry("http://localhost:15476/chat?sid=octopus-run-1&token=t");
    expect(() => validateCrewEntry(local, "octopus-run-1", "d1.cloudfront.net")).toThrow("指向本机");
    expect(validateCrewEntry(local, "octopus-run-1", "127.0.0.1").port).toBe("15476");
  });
});
