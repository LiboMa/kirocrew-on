import { afterEach, describe, expect, test, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { setToken } from "@/api/client";
import type { KnowledgeItem, Run } from "@/api/types";
import { makeRun, mockApi } from "@/test/api";
import { KnowledgePanel } from "@/features/sessions/KnowledgePanel";

const item = (overrides: Partial<KnowledgeItem> = {}): KnowledgeItem => ({
  id: "knowledge-1", task_id: "knowledge", rule: "依赖用 pnpm 安装", avoid: "不要用 npm install", result: "written", reason: null,
  created: 1, removed: null, removed_by: null, ...overrides });

afterEach(() => vi.unstubAllGlobals());

function show(run: Run) {
  setToken("oct_fixture");
  const calls = mockApi({ [`POST /runs/${run.id}/knowledge/forget`]: { id: "knowledge-1", removed: true } });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><KnowledgePanel run={run} /></QueryClientProvider>);
  return calls;
}

describe("knowledge panel (D46 P4)", () => {
  test("lists what was written and why the rest was not", () => {
    show(makeRun({ knowledge: { note: null, items: [item(), item({ id: "knowledge-2", rule: "（含疑似秘密，内容未保存）", avoid: null,
      result: "skipped", reason: "含疑似秘密，未写入" })] } }));
    const panel = screen.getByRole("region", { name: "本次沉淀的知识" });
    expect(within(panel).getByText("依赖用 pnpm 安装")).toBeInTheDocument();
    expect(within(panel).getByText("不要：不要用 npm install")).toBeInTheDocument();
    expect(within(panel).getByText("未写入：含疑似秘密，未写入")).toBeInTheDocument();
    expect(within(panel).getAllByRole("button", { name: "删除" })).toHaveLength(1);   // only a stored item can be deleted
  });

  test("deleting asks once more, then forgets that item", async () => {
    const calls = show(makeRun({ knowledge: { note: null, items: [item()] } }));
    const panel = screen.getByRole("region", { name: "本次沉淀的知识" });
    await userEvent.click(within(panel).getByRole("button", { name: "删除" }));
    expect(calls.some((c) => c.path.endsWith("/knowledge/forget"))).toBe(false);
    await userEvent.click(within(panel).getByRole("button", { name: "确认删除" }));
    await waitFor(() => expect(calls.find((c) => c.path.endsWith("/knowledge/forget"))?.body).toEqual({ id: "knowledge-1" }));
  });

  test("a removed item says who removed it and offers nothing", () => {
    show(makeRun({ knowledge: { note: null, items: [item({ removed: 2, removed_by: "owner" })] } }));
    const panel = screen.getByRole("region", { name: "本次沉淀的知识" });
    expect(within(panel).getByText("已删除（owner）")).toBeInTheDocument();
    expect(within(panel).queryByRole("button", { name: "删除" })).not.toBeInTheDocument();
  });

  test("someone who may not publish sees no delete", () => {
    show(makeRun({ permissions: { operate: true, delete: false, native: true, resume: true, publish: false },
      knowledge: { note: null, items: [item()] } }));
    expect(screen.queryByRole("button", { name: "删除" })).not.toBeInTheDocument();
  });

  test("a shared environment says it gathers nothing", () => {
    show(makeRun({ knowledge: { note: "共享执行环境不沉淀、不注入知识", items: [] } }));
    expect(screen.getByText("共享执行环境不沉淀、不注入知识")).toBeInTheDocument();
  });

  test("a Session without knowledge shows no panel", () => {
    show(makeRun({ knowledge: null }));
    expect(screen.queryByRole("region", { name: "本次沉淀的知识" })).not.toBeInTheDocument();
  });
});
