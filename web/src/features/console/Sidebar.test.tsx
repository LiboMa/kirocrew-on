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
