import { afterEach, describe, expect, test, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "@/App";
import { setToken } from "@/api/client";
import { makeRun, mockApi, runPage, workspaceRoutes } from "@/test/api";

afterEach(() => {
  vi.unstubAllGlobals();
  window.history.replaceState(null, "", "/");
});

const runs = [
  makeRun({ id: "run-a", name: "修复登录失败", status: "blocked", created: 1_790_000_300 }),
  makeRun({ id: "run-b", name: "订单缓存", status: "running", created: 1_790_000_200 }),
];

function start(handler?: (url: URL) => unknown) {
  const searches: URLSearchParams[] = [];
  setToken("oct_fixture");
  mockApi(workspaceRoutes(runs, { "GET /runs": (_body: unknown, url: URL) => {
    searches.push(new URLSearchParams(url.search));
    return handler ? handler(url) : runPage(runs, url);
  } }));
  render(<App />);
  const paged = () => searches.filter((s) => s.get("page") === "1" && s.get("limit") !== "1");
  return { searches, paged };
}

describe("session list on the server (D56)", () => {
  test("the search asks the server once the typing pauses", async () => {
    const { paged } = start();
    await screen.findByRole("navigation", { name: "会话列表" });
    await userEvent.type(screen.getByLabelText("搜索会话"), "缓存");
    await waitFor(() => expect(paged().some((s) => s.get("q") === "缓存")).toBe(true));
    expect(paged().some((s) => s.get("q") === "缓")).toBe(false);
  });

  test("a state group is sent as statuses and the lifecycle", async () => {
    const { paged } = start();
    await screen.findByRole("navigation", { name: "会话列表" });
    await userEvent.click(screen.getByRole("button", { name: "需处理" }));
    await waitFor(() => expect(paged().some((s) => s.get("status")?.split(",").includes("blocked") && s.get("lifecycle") === "open"))
      .toBe(true));
  });

  test("「进行中」 asks only for statuses a Session has, and finds the running one (final review)", async () => {
    const { paged } = start();
    const list = await screen.findByRole("navigation", { name: "会话列表" });
    await userEvent.click(screen.getByRole("button", { name: "进行中" }));
    await waitFor(() => expect(within(list).getAllByRole("button").filter((b) => b.dataset.run).map((b) => b.dataset.run)).toEqual(["run-b"]));
    expect(paged().at(-1)?.get("status")?.split(",")).not.toContain("dispatching");
  });

  test("a list the server refuses says so instead of looking empty", async () => {
    start((url) => url.searchParams.get("q") === "坏"
      ? new Response(JSON.stringify({ error: "q 须为 1–200 个字符", code: "invalid_request" }), { status: 400 }) : runPage(runs, url));
    await screen.findByRole("navigation", { name: "会话列表" });
    await userEvent.type(screen.getByLabelText("搜索会话"), "坏");
    expect(await screen.findByRole("alert")).toHaveTextContent("q 须为 1–200 个字符");
  });

  test("the creation dates are the user's own days", async () => {
    const { paged } = start();
    await screen.findByRole("navigation", { name: "会话列表" });
    await userEvent.click(screen.getByRole("button", { name: "筛选" }));
    await userEvent.type(screen.getByLabelText("创建于起"), "2026-10-21");
    await userEvent.type(screen.getByLabelText("创建于止"), "2026-10-21");
    const since = String(new Date(2026, 9, 21).getTime() / 1000);
    const until = String(new Date(2026, 9, 22).getTime() / 1000 - 1);
    await waitFor(() => expect(paged().some((s) => s.get("since") === since && s.get("until") === until)).toBe(true));
    expect(new URLSearchParams(window.location.search).get("since")).toBe("2026-10-21");
  });

  test("typing a repository is kept while another filter changes", async () => {
    const { paged } = start();
    await screen.findByRole("navigation", { name: "会话列表" });
    await userEvent.click(screen.getByRole("button", { name: "筛选" }));
    await userEvent.type(screen.getByLabelText("仓库"), "github.com/o/r");
    await userEvent.selectOptions(screen.getByLabelText("工具"), "codex");
    expect(screen.getByLabelText("仓库")).toHaveValue("github.com/o/r");
    await waitFor(() => expect(paged().some((s) => s.get("repo") === "github.com/o/r" && s.get("tool") === "codex")).toBe(true));
  });

  test("filters are sent, shown as removable labels and kept in the address", async () => {
    const { paged } = start();
    await screen.findByRole("navigation", { name: "会话列表" });
    await userEvent.click(screen.getByRole("button", { name: "筛选" }));
    await userEvent.selectOptions(screen.getByLabelText("工具"), "codex");
    await userEvent.selectOptions(screen.getByLabelText("提交者"), "fixture-owner");
    await userEvent.type(screen.getByLabelText("仓库"), "github.com/o/r");
    await waitFor(() => expect(paged().some((s) => s.get("tool") === "codex" && s.get("submitter") === "fixture-owner"
      && s.get("repo") === "github.com/o/r")).toBe(true));
    expect(new URLSearchParams(window.location.search).get("tool")).toBe("codex");
    const chips = screen.getByRole("group", { name: "已选筛选" });
    await userEvent.click(within(chips).getByRole("button", { name: "移除 工具：codex" }));
    await waitFor(() => expect(paged().at(-1)?.get("tool")).toBeNull());
    await userEvent.click(within(chips).getByRole("button", { name: "清除筛选" }));
    await waitFor(() => expect(paged().at(-1)?.get("submitter")).toBeNull());
  });

  test("more is loaded from the cursor and the tab counts the server's total", async () => {
    start((url) => {
      if (url.searchParams.get("limit") === "1") return { items: [], next: null, total: 7 };
      return url.searchParams.get("cursor") === "c1"
        ? { items: [makeRun({ id: "run-old", name: "更早的会话" })], next: null, total: 7 }
        : { items: runs, next: "c1", total: 7 };
    });
    const list = await screen.findByRole("navigation", { name: "会话列表" });
    expect(await screen.findByRole("tab", { name: /会话 7/ })).toBeInTheDocument();
    await userEvent.click(await screen.findByRole("button", { name: "加载更多" }));
    expect(await within(list).findByText("更早的会话")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "加载更多" })).toBeNull();
  });

  test("a selected Session beyond the loaded pages is read by its id", async () => {
    const older = makeRun({ id: "run-z", name: "很早的会话" });
    setToken("oct_fixture");
    window.history.replaceState(null, "", "/?run=run-z");
    mockApi(workspaceRoutes(runs, { "GET /runs/run-z": older, "GET /runs/run-z/events": [], "GET /runs/run-z/app": { status: "stopped", log: [], openable: false },
      "GET /runs/run-z/approvals": { approvals: [], supported: true } }));
    render(<App />);
    expect(await screen.findByRole("heading", { name: "很早的会话" })).toBeInTheDocument();
    expect(window.location.search).toContain("run=run-z");
  });
});


// Owner 2026-10-22: 「历史 Session，请增加收藏、置顶、删除、分享功能」
describe("pin, star, share and delete from the list", () => {
  function begin(marks: Record<string, { pinned: boolean; starred: boolean }> = {}) {
    const searches: URLSearchParams[] = [];
    const marked = (run: (typeof runs)[number]) => ({ ...run, marks: marks[run.id] ?? { pinned: false, starred: false },
      permissions: { operate: true, delete: true, share: true } });
    setToken("oct_fixture");
    const calls = mockApi(workspaceRoutes(runs, {
      "GET /runs": (_body: unknown, url: URL) => {
        searches.push(new URLSearchParams(url.search));
        if (url.searchParams.get("pinned") === "1") return runs.filter((r) => marks[r.id]?.pinned).map(marked);
        return runPage(runs.map(marked), url);
      },
      "POST /runs/run-a/marks": (body: unknown) => {
        marks["run-a"] = { ...(marks["run-a"] ?? { pinned: false, starred: false }), ...(body as object) };
        return marks["run-a"];
      },
    }));
    render(<App />);
    return { searches, calls };
  }

  test("a row's menu pins a Session, which then shows on top", async () => {
    const { calls } = begin();
    await screen.findByRole("navigation", { name: "会话列表" });
    await userEvent.click(await screen.findByRole("button", { name: "会话操作：修复登录失败" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: "置顶" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path === "/v1/runs/run-a/marks")?.body).toEqual({ pinned: true }));
    const pinned = await screen.findByRole("region", { name: "置顶" });
    expect(await within(pinned).findByText("修复登录失败")).toBeInTheDocument();
  });

  test("「收藏」 lists the starred, and a starred or pinned Session says so", async () => {
    const { searches } = begin({ "run-b": { pinned: false, starred: true } });
    const list = await screen.findByRole("navigation", { name: "会话列表" });
    expect(await within(list).findByLabelText("已收藏")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "收藏" }));
    await waitFor(() => expect(searches.some((s) => s.get("starred") === "1" && s.get("page") === "1")).toBe(true));
  });

  test("share and delete are in the row's menu", async () => {
    begin();
    await screen.findByRole("navigation", { name: "会话列表" });
    await userEvent.click(await screen.findByRole("button", { name: "会话操作：订单缓存" }));
    await userEvent.click(await screen.findByRole("menuitem", { name: /删除/ }));
    expect(await screen.findByRole("dialog")).toHaveTextContent("run-b");
  });
});
