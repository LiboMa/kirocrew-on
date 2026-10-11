import { getToken } from "@/api/client";
import type { RunEvent } from "@/api/types";

export type StreamState = "connecting" | "connected" | "reconnecting" | "revoked";

/** Splits a text/event-stream buffer into complete blocks; returns the events and the unconsumed remainder. */
export function parseEventBlocks(buffer: string): { events: RunEvent[]; revoked: boolean; rest: string } {
  const events: RunEvent[] = [];
  let revoked = false;
  let rest = buffer;
  let boundary: number;
  while ((boundary = rest.indexOf("\n\n")) >= 0) {
    const block = rest.slice(0, boundary);
    rest = rest.slice(boundary + 2);
    if (block.split("\n").some((line) => line.trim() === "event: access_revoked")) {
      revoked = true;
      continue;
    }
    const data = block.split("\n").find((line) => line.startsWith("data:"));
    if (!data) continue;
    try {
      events.push(JSON.parse(data.slice(5).trim()) as RunEvent);
    } catch {
      // A malformed block is skipped; the next reconnect resumes from the last good id.
    }
  }
  return { events, revoked, rest };
}

interface Options {
  after?: number;
  signal: AbortSignal;
  onEvent: (event: RunEvent) => void;
  onState: (state: StreamState) => void;
  retryDelay?: number;
}

/** Follows /v1/runs/{id}/events?stream=1 with resume (`after`) until aborted or access is revoked. */
export async function followRunEvents(runId: string, { after = 0, signal, onEvent, onState, retryDelay = 1500 }: Options) {
  let last = after;
  while (!signal.aborted) {
    onState(last ? "reconnecting" : "connecting");
    try {
      const response = await fetch(
        `/v1/runs/${encodeURIComponent(runId)}/events?stream=1&after=${last}`,
        { headers: { Authorization: "Bearer " + getToken() }, signal },
      );
      if (response.status === 401 || response.status === 403 || response.status === 404) {
        onState("revoked");
        return;
      }
      if (!response.ok || !response.body) throw new Error("stream unavailable");
      onState("connected");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        const parsed = parseEventBlocks(buffer + decoder.decode(value, { stream: true }));
        buffer = parsed.rest;
        for (const event of parsed.events) {
          if (event.id <= last) continue;
          last = event.id;
          if (!signal.aborted) onEvent(event);
        }
        if (parsed.revoked) {
          onState("revoked");
          return;
        }
      }
    } catch {
      if (signal.aborted) return;
    }
    if (signal.aborted) return;
    onState("reconnecting");
    await new Promise((resolve) => setTimeout(resolve, retryDelay));
  }
}
