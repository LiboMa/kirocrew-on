import { afterEach, describe, expect, test, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App } from "@/App";
import { setToken } from "@/api/client";
import { mockApi, workspaceRoutes } from "@/test/api";
import { openSection } from "@/test/pages";

const PENDING = { id: "awr-1", kind: "native", params: { boundary: "bob" }, state: "pending", created: 1_790_000_000, finished: null,
  result: null, command: "kirocrew-on single native bob --request awr-1 --platform https://d1.cloudfront.net --token-file <管理员 API Token 文件>" };
const DONE = { id: "awr-0", kind: "backup", params: { bucket: "my-backups" }, state: "done", created: 1_789_000_000, finished: 1_789_000_100,
  result: { bucket: "my-backups", key: "kirocrew-on/backups/i-0abc/20261027T030000Z.tar.gz" },
  command: "kirocrew-on single backup --bucket my-backups --request awr-0 --platform https://d1.cloudfront.net --token-file <管理员 API Token 文件>" };

function start(extra: Record<string, unknown> = {}) {
  setToken("oct_fixture");
  const calls = mockApi(workspaceRoutes([], extra));
  render(<App />);
  return calls;
}

afterEach(() => vi.unstubAllGlobals());

describe("管理 › 部署 (MVP 0.2.0 P2, S29)", () => {
  test("lists what the deployment kit is to do, with the command to run off the host, and records a new one", async () => {
    let made = false;
    const calls = start({ "GET /admin/aws-requests": () => ({ items: made ? [PENDING, DONE] : [PENDING, DONE] }),
      "POST /admin/aws-requests": () => { made = true; return { ...PENDING, id: "awr-2", kind: "up", params: {} }; } });
    const page = await openSection("管理", "部署");
    const pending = (await within(page).findByText("给边界开公网原生入口")).closest("li") as HTMLElement;
    expect(within(pending).getByText("待执行")).toBeInTheDocument();
    expect(within(pending).getByText(PENDING.command)).toBeInTheDocument();
    expect(within(pending).getByRole("button", { name: "复制命令：awr-1" })).toBeInTheDocument();
    const done = (within(page).getByText("异地备份")).closest("li") as HTMLElement;
    expect(within(done).getByText("已完成")).toBeInTheDocument();
    expect(within(done).getByText(/20261027T030000Z\.tar\.gz/)).toBeInTheDocument();
    expect(within(done).queryByRole("button", { name: /^复制命令/ })).toBeNull();
    await userEvent.click(within(page).getByRole("button", { name: "记录变更" }));
    const dialog = await screen.findByRole("dialog", { name: "记录变更" });
    await userEvent.selectOptions(within(dialog).getByLabelText("变更"), "backup");
    await userEvent.type(within(dialog).getByLabelText("S3 桶"), "my-backups");
    await userEvent.click(within(dialog).getByRole("button", { name: "记录" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path === "/v1/admin/aws-requests")?.body)
      .toEqual({ kind: "backup", params: { bucket: "my-backups" } }));
    expect(await screen.findByText("已记录：异地备份")).toBeInTheDocument();
  });

  test("a host role that can still write stays in red until it is trimmed", async () => {
    start({ "GET /admin/aws-requests": { items: [], host_role: { state: "writes", role: "kirocrew-on-host",
      allowed: ["cloudformation:CreateStack"], commands: ["aws iam detach-role-policy --role-name kirocrew-on-host --policy-arn arn:x"],
      checked: 1_790_000_000 } } });
    const page = await openSection("管理", "部署");
    const alert = await within(page).findByRole("alert");
    expect(alert).toHaveTextContent("主机角色 kirocrew-on-host 仍有写权限");
    expect(alert).toHaveTextContent("cloudformation:CreateStack");
    expect(alert).toHaveTextContent("aws iam detach-role-policy --role-name kirocrew-on-host --policy-arn arn:x");
  });

  test("nothing waiting says how changes get here", async () => {
    start({ "GET /admin/aws-requests": { items: [] } });
    const page = await openSection("管理", "部署");
    expect(await within(page).findByText(/还没有待执行的 AWS 变更/)).toBeInTheDocument();
  });
});
