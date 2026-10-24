import { useCallback, useState } from "react";

const reduced = () => typeof window !== "undefined" && !!window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

/** PRD 2026-10-23 (管理 / 我的设置 in dialogs): a list row the person just made lights up once (``flash``), and one they
 * removed fades out before the list drops it (``leave``); none of it under 「减少动态效果」. ``props(id)`` goes on the row. */
export function useRowMotion() {
  const [fresh, setFresh] = useState<string | null>(null);
  const [leaving, setLeaving] = useState<string | null>(null);
  const flash = useCallback((id: string) => {
    setFresh(id);
    window.setTimeout(() => setFresh((current) => (current === id ? null : current)), 1400);
  }, []);
  const leave = useCallback(async (id: string, then: () => Promise<unknown>) => {
    setLeaving(id);
    await new Promise((resolve) => window.setTimeout(resolve, reduced() ? 0 : 200));
    try {
      await then();
    } finally {
      setLeaving(null);
    }
  }, []);
  const props = (id: string) => ({
    "data-fresh": fresh === id ? "true" : undefined,
    className: fresh === id ? "row-flash" : leaving === id ? "row-leave" : "",
  });
  return { flash, leave, props };
}
