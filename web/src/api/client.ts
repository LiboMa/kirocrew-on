// Thin client for the platform API. The token lives in sessionStorage under the same key as the classic console,
// so both consoles share one login; a 401 anywhere except /login signs the user out.

const TOKEN_KEY = "octopus.token";
type Listener = (token: string) => void;
const listeners = new Set<Listener>();

export function getToken(): string {
  return sessionStorage.getItem(TOKEN_KEY) ?? "";
}

export function setToken(token: string) {
  if (token) sessionStorage.setItem(TOKEN_KEY, token);
  else sessionStorage.removeItem(TOKEN_KEY);
  listeners.forEach((listener) => listener(token));
}

export function onTokenChange(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, message: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
  }
}

type Method = "GET" | "POST" | "PATCH" | "DELETE";

export async function api<T>(path: string, method: Method = "GET", body?: unknown): Promise<T> {
  let response: Response;
  try {
    response = await fetch("/v1" + path, {
      method,
      cache: "no-store",
      headers: {
        Authorization: "Bearer " + getToken(),
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch {
    throw new ApiError(0, "network", "无法连接平台 API，请检查网络后重试。");
  }
  let result: unknown = null;
  try {
    result = await response.json();
  } catch {
    // A proxy (CloudFront) answered for an unavailable origin with a non-JSON page.
  }
  if (!response.ok) {
    if (response.status === 401 && path !== "/login") setToken("");
    const detail = (result ?? {}) as { error?: string; code?: string };
    const message =
      detail.error ?? (response.status >= 500 ? `${response.status} API 暂不可用，稍后重试` : `请求失败（${response.status}）`);
    throw new ApiError(response.status, detail.code ?? "http_" + response.status, message);
  }
  return result as T;
}

// Commands carry a request_id; a retry of the same command (same path and body) reuses it, so the platform executes
// it once even when the first response was lost.
const pending = new Map<string, string>();

export async function command<T>(path: string, body: Record<string, unknown>): Promise<T> {
  const signature = path + JSON.stringify(body);
  if (!pending.has(signature)) pending.set(signature, crypto.randomUUID());
  const value = await api<T>(path, "POST", { ...body, request_id: pending.get(signature) });
  pending.delete(signature);
  return value;
}
