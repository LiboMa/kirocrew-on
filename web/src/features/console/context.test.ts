import { describe, expect, test } from "vitest";
import type { Worker } from "@/api/types";
import { spaceWorkers } from "@/features/console/context";

const me = { id: "u1", username: "me", admin: 0, scopes: [] };
const worker = (overrides: Partial<Worker>): Worker =>
  ({ id: "w", name: "w", owner_id: null, team_id: null, enabled: true, scope: "dedicated", spaces: [], capabilities: {}, ...overrides } as Worker);

describe("spaceWorkers", () => {
  test("a shared environment serves exactly the spaces registered for it", () => {
    const shared = worker({ id: "s", scope: "shared", owner_id: "admin", spaces: [{ team_id: "t1" }, { user_id: "u1" }] });
    expect(spaceWorkers([shared], "t1", me).map((w) => w.id)).toEqual(["s"]);
    expect(spaceWorkers([shared], "", me).map((w) => w.id)).toEqual(["s"]);
    expect(spaceWorkers([shared], "t2", me)).toEqual([]);
    expect(spaceWorkers([shared], "", { ...me, id: "u2" })).toEqual([]);
  });

  test("dedicated Workers keep their owner or Team, spare and disabled ones are never usable", () => {
    const mine = worker({ id: "d", owner_id: "u1" });
    const team = worker({ id: "t", team_id: "t1" });
    const spare = worker({ id: "sp", scope: "spare", owner_id: "u1" });
    const off = worker({ id: "off", owner_id: "u1", enabled: false });
    expect(spaceWorkers([mine, team, spare, off], "", me).map((w) => w.id)).toEqual(["d"]);
    expect(spaceWorkers([mine, team, spare, off], "t1", me).map((w) => w.id)).toEqual(["t"]);
  });
});
