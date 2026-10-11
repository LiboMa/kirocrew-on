import { useCallback, useEffect, useRef, useState } from "react";
import { api, ApiError, command } from "@/api/client";
import type { Resolution, Step, Template, Workflow } from "@/api/types";
import { fingerprint, nameFrom, saveDraft, type Draft, type Generation } from "@/features/launch/state";

type Update = Draft | ((current: Draft) => Draft);

/** Draft state readable synchronously (validation right after an await) and saved on every change — also after the
 *  wizard closed, so a late generation receipt still lands in its own draft. */
export function useDraft(key: string, initial: () => Draft) {
  const ref = useRef<Draft | null>(null);
  if (ref.current === null) ref.current = initial();
  const [draft, setState] = useState<Draft>(ref.current);
  const [storageError, setStorageError] = useState(false);
  const update = useCallback((next: Update) => {
    const value = typeof next === "function" ? next(ref.current!) : next;
    ref.current = value;
    setState(value);
    setStorageError(!saveDraft(key, value));
  }, [key]);
  return { draft, ref: ref as { current: Draft }, update, storageError };
}

export interface MatchStatus {
  title: string;
  reason: string;
  alternatives: NonNullable<Resolution["recommendations"]>;
  pending: boolean;
}

const selectionFor = (choice: string) =>
  choice.startsWith("workflow:") ? { workflow_id: choice.slice(9) } :
    choice.startsWith("template:") ? { template_id: choice.slice(9) } : {};

/** Rule-based matching of the requirement to templates and saved workflows; never calls a model. */
export function useMatch(ref: { current: Draft }, update: (next: Update) => void,
  { templates, workflows }: { templates: Template[]; workflows: Workflow[] }) {
  const sequence = useRef(0);
  const [status, setStatus] = useState<MatchStatus>({ title: "等待输入需求", reason: "输入至少 3 个字符，自动显示工作流与任务。", alternatives: [], pending: false });

  const match = useCallback(async () => {
    const id = ++sequence.current;
    const { input: raw, choice, space } = ref.current;
    const input = raw.trim();
    if (choice === "custom") return;
    if (input.length < 3) {
      const preset = choice.startsWith("template:") ? templates.find((t) => t.id === choice.slice(9))
        : workflows.find((f) => "workflow:" + f.id === choice);
      update((d) => ({ ...d, tasks: structuredClone(preset?.steps ?? []), resolution: null }));
      setStatus({ title: preset ? "已载入预设：" + preset.name : "等待输入需求", alternatives: [], pending: false,
        reason: preset ? "预设提示词已可编辑；填写需求后可以由后台 Kiro Agent 进一步推荐。" : "输入至少 3 个字符，自动显示工作流与任务。" });
      return;
    }
    setStatus((s) => ({ ...s, title: choice === "auto" ? "正在匹配工作流…" : "正在读取选定工作流…", reason: "匹配不会创建 Session 或派发 Coding 任务。", pending: true }));
    try {
      const result = await api<Resolution>("/workflows/resolve", "POST", { input, ...selectionFor(choice), ...(space ? { team_id: space } : {}) });
      const now = ref.current;
      if (id !== sequence.current || now.input.trim() !== input || now.choice !== choice || now.space !== space) return;
      update((d) => ({ ...d, resolution: result, tasks: structuredClone(result.steps), name: d.nameTouched ? d.name : result.name }));
      setStatus({ title: (choice === "auto" ? "已匹配：" : "已指定：") + result.workflow_name, reason: result.reason,
        alternatives: (result.recommendations ?? []).slice(1, 4), pending: false });
    } catch (error) {
      if (id !== sequence.current) return;
      update((d) => ({ ...d, tasks: [], resolution: null }));
      setStatus({ title: "暂未得到匹配结果", reason: (error as Error).message, alternatives: [], pending: false });
    }
  }, [ref, update, templates, workflows]);

  /** Call after the requirement or choice changed; debounced like typing. */
  const schedule = useCallback(() => {
    sequence.current++;
    const timer = setTimeout(() => void match(), 350);
    return () => clearTimeout(timer);
  }, [match]);

  /** "Next" right after editing the requirement waits for that requirement's match instead of failing. */
  const settle = useCallback(async () => {
    const d = ref.current;
    if (d.choice !== "custom" && d.resolution?.input !== d.input.trim()) await match();
  }, [ref, match]);

  const describe = useCallback((title: string, reason: string) => {
    sequence.current++;
    setStatus({ title, reason, alternatives: [], pending: false });
  }, []);

  return { status, match, schedule, settle, describe };
}

const TERMINAL = new Set(["ready", "invalid", "blocked", "failed", "stopped", "cancelled"]);

export function generationActive(job: Generation | null) {
  return !!job && !TERMINAL.has(job.status) && job.status !== "uncertain";
}

/** A real Kiro Agent designs tasks and prompts through /v1/designs; the result is only applied when the user adopts it. */
export function useRecommendation(ref: { current: Draft }, update: (next: Update) => void) {
  const alive = useRef(true);
  const timer = useRef<ReturnType<typeof setTimeout>>(undefined);
  useEffect(() => () => {
    alive.current = false;
    clearTimeout(timer.current);
  }, []);

  const setJob = useCallback((change: (job: Generation) => Generation, requestId?: string) => {
    update((d) => {
      const job = d.generation;
      if (!job || (requestId && job.request?.request_id !== requestId)) return d;
      return { ...d, generation: change(job) };
    });
  }, [update]);

  const poll = useCallback(async () => {
    clearTimeout(timer.current);
    const job = ref.current.generation;
    if (!alive.current || !job?.id || TERMINAL.has(job.status)) return;
    const id = job.id;
    try {
      const result = await api<Generation>("/designs/" + encodeURIComponent(id));
      setJob((current) => current.id === id ? { ...current, ...result, poll_error: undefined } : current);
    } catch (error) {
      setJob((current) => current.id === id ? { ...current, poll_error: (error as Error).message } : current);
    }
    const now = ref.current.generation;
    if (alive.current && now?.id === id && !TERMINAL.has(now.status)) timer.current = setTimeout(() => void poll(), 2000);
  }, [ref, setJob]);

  useEffect(() => {
    // Resume a generation that was still running when the wizard was last closed.
    if (generationActive(ref.current.generation)) void poll();
  }, [ref, poll]);

  const start = useCallback(async (worker: string) => {
    const d = ref.current;
    let job = d.generation;
    if (job?.status !== "uncertain") {
      if (d.input.trim().length < 3) throw new Error("请先填写研发需求。");
      if (!d.tasks.length || (d.choice !== "custom" && d.resolution?.input !== d.input.trim()))
        throw new Error("请先等待模板匹配完成，或编辑任务列表。");
      if (!worker) throw new Error("此空间尚无可用 Worker，无法生成建议。");
      const request = { request_id: crypto.randomUUID(), input: d.input.trim(), steps: d.tasks, worker_id: worker,
        ...(d.space ? { team_id: d.space } : {}) };
      job = { request, fingerprint: fingerprint(d.input, d.tasks, d.space), status: "submitting" };
      update({ ...d, generation: job });
    }
    const requestId = job!.request!.request_id;
    try {
      const result = await api<Generation>("/designs", "POST", job!.request);
      setJob((current) => ({ ...current, ...result, status: result.status, error: undefined }), requestId);
      void poll();
    } catch (error) {
      const definite = error instanceof ApiError && [400, 403, 404, 409, 422].includes(error.status);
      setJob((current) => ({ ...current, status: definite ? "failed" : "uncertain", error: (error as Error).message }), requestId);
    }
  }, [ref, update, setJob, poll]);

  const apply = useCallback(() => {
    update((d) => {
      const job = d.generation;
      if (!job?.config || job.status !== "ready" || job.fingerprint !== fingerprint(d.input, d.tasks, d.space)) return d;
      return { ...d, tasks: structuredClone(job.config.steps) as Step[], choice: "custom", resolution: null,
        name: d.nameTouched ? d.name : job.config.name || nameFrom(d.input), generation: { ...job, applied: true } };
    });
  }, [update]);

  const stop = useCallback(async () => {
    const job = ref.current.generation;
    if (!job?.id) return;
    const result = await command<{ version: number }>("/runs/" + job.id + "/stop", { expected_version: job.version ?? 1 });
    setJob((current) => ({ ...current, version: result.version, status: "stopping" }));
    void poll();
  }, [ref, setJob, poll]);

  return { start, apply, stop };
}
