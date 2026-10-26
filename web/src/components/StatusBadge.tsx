import { cn } from "@/lib/utils";
import { STATES, stateTone, type Tone } from "@/lib/labels";

const TONES: Record<Tone, string> = {
  neutral: "bg-muted text-muted-foreground border-border",
  active: "bg-success/10 text-success border-success/20",
  done: "bg-done/10 text-done border-done/20",
  warning: "bg-warning/10 text-warning border-warning/25",
  danger: "bg-destructive/10 text-destructive border-destructive/20",
};

export function StatusBadge({ status, label, className }: { status: string; label?: string; className?: string }) {
  const tone = stateTone(status);
  return (
    <span
      data-state={status}
      className={cn("inline-flex shrink-0 items-center gap-1.5 rounded-full border px-2 py-0.5 text-[11px] font-medium", TONES[tone], className)}
    >
      {tone === "active" && <span className="size-1.5 animate-pulse rounded-full bg-current" aria-hidden />}
      {label ?? STATES[status] ?? status}
    </span>
  );
}
