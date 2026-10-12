import { afterEach, describe, expect, test, vi } from "vitest";
import { ApiError, api, command, getToken, onTokenChange, setToken } from "@/api/client";
import { followRunEvents, parseEventBlocks } from "@/api/events";

const json = (status: number, body: unknown) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

afterEach(() => vi.unstubAllGlobals());

describe("api client", () => {
  test("sends the shared console token and returns JSON", async () => {
    setToken("oct_fixture");
    const fetchMock = vi.fn().mockResolvedValue(json(200, { id: "u1" }));
    vi.stubGlobal("fetch", fetchMock);
    expect(await api("/me")).toEqual({ id: "u1" });
    expect(fetchMock.mock.calls[0][0]).toBe("/v1/me");
    expect(fetchMock.mock.calls[0][1].headers.Authorization).toBe("Bearer oct_fixture");
    expect(sessionStorage.getItem("octopus.token")).toBe("oct_fixture");
  });

  test("a 401 signs out everywhere, except on the login call", async () => {
    setToken("oct_fixture");
    const seen: string[] = [];
    const stop = onTokenChange((token) => seen.push(token));
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(401, { error: "用户名或密码错误", code: "unauthorized" })));
    await expect(api("/login", "POST", {})).rejects.toMatchObject({ status: 401, message: "用户名或密码错误" });
    expect(getToken()).toBe("oct_fixture");
    await expect(api("/runs")).rejects.toBeInstanceOf(ApiError);
    expect(getToken()).toBe("");
    expect(seen).toEqual([""]);
    stop();
  });

  test("server reasons are kept; proxy pages and network failures stay readable", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(json(409, { error: "工作流已归档", code: "archived" })));
    await expect(api("/runs", "POST", {})).rejects.toMatchObject({ status: 409, code: "archived", message: "工作流已归档" });
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response("<html>504</html>", { status: 504 })));
    await expect(api("/runs")).rejects.toMatchObject({ status: 504, message: "504 API 暂不可用，稍后重试" });
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(api("/runs")).rejects.toMatchObject({ status: 0, code: "network" });
  });

  test("a retried command reuses its request_id; the next command gets a new one", async () => {
    const bodies: { request_id: string }[] = [];
    const fetchMock = vi.fn(async (_url: string, init: RequestInit) => {
      bodies.push(JSON.parse(init.body as string));
      return bodies.length === 1 ? json(503, { error: "Worker 暂不可用", code: "worker_unavailable" }) : json(200, { ok: true });
    });
    vi.stubGlobal("fetch", fetchMock);
    await expect(command("/runs/r/stop", { expected_version: 1 })).rejects.toBeInstanceOf(ApiError);
    await command("/runs/r/stop", { expected_version: 1 });
    await command("/runs/r/stop", { expected_version: 1 });
    expect(bodies[0].request_id).toBe(bodies[1].request_id);
    expect(bodies[2].request_id).not.toBe(bodies[1].request_id);
  });
});

describe("run event stream", () => {
  test("parses complete blocks, keeps partial input and recognises revocation", () => {
    const first = parseEventBlocks('id: 1\ndata: {"id":1,"kind":"run.created"}\n\ndata: {"id":2');
    expect(first.events.map((e) => e.id)).toEqual([1]);
    expect(first.rest).toBe('data: {"id":2');
    const second = parseEventBlocks(first.rest + ',"kind":"run.state"}\n\nevent: access_revoked\ndata: {}\n\ndata: {bad\n\n');
    expect(second.events.map((e) => e.id)).toEqual([2]);
    expect(second.revoked).toBe(true);
  });

  test("resumes after the last event id and never repeats an event", async () => {
    const urls: string[] = [];
    const bodies = ['data: {"id":1,"kind":"a"}\n\ndata: {"id":2,"kind":"b"}\n\n', 'data: {"id":2,"kind":"b"}\n\ndata: {"id":3,"kind":"c"}\n\n'];
    const controller = new AbortController();
    const seen: number[] = [];
    vi.stubGlobal("fetch", vi.fn(async (url: string) => {
      urls.push(url);
      const body = bodies.shift();
      if (body === undefined) {
        controller.abort();
        throw new DOMException("aborted", "AbortError");
      }
      return new Response(body, { status: 200 });
    }));
    await followRunEvents("run-1", { signal: controller.signal, retryDelay: 1, onState: () => {}, onEvent: (e) => seen.push(e.id) });
    expect(seen).toEqual([1, 2, 3]);
    expect(urls[0]).toContain("after=0");
    expect(urls[1]).toContain("after=2");
  });

  test("stops when access is gone instead of retrying forever", async () => {
    const states: string[] = [];
    const fetchMock = vi.fn().mockResolvedValue(new Response("{}", { status: 403 }));
    vi.stubGlobal("fetch", fetchMock);
    await followRunEvents("run-1", { signal: new AbortController().signal, retryDelay: 1, onEvent: () => {}, onState: (s) => states.push(s) });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(states.at(-1)).toBe("revoked");
  });
});
