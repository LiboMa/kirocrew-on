import { useId, useState, type FormEvent, type ReactNode } from "react";
import { Button } from "@/components/ui/button";

interface Props {
  title: string;
  description?: ReactNode;
  /** Buttons beside the title (a refresh, 「新建用户」). */
  actions?: ReactNode;
  children?: ReactNode;
  submitLabel?: string;
  onSubmit?: () => Promise<void> | void;
  footer?: ReactNode;
}

/** One section of the 管理 / 我的设置 pages: what a settings dialog used to hold, on the page. With ``onSubmit`` the
 * section is a form whose button sits at its end; the server's reason for a failure is shown above it. */
export function Panel({ title, description, actions, children, submitLabel = "保存", onSubmit, footer }: Props) {
  const heading = useId();
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    if (!onSubmit || busy) return;
    setBusy(true);
    setError("");
    try {
      await onSubmit();
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby={heading} data-panel className="space-y-5">
      <header className="flex flex-wrap items-start justify-between gap-3 border-b pb-4">
        <div className="min-w-0 flex-1 basis-80 space-y-1">
          <h2 id={heading} className="text-xl font-semibold tracking-tight">{title}</h2>
          {description && <p className="max-w-3xl text-sm leading-6 text-muted-foreground">{description}</p>}
        </div>
        {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
      </header>
      {onSubmit ? (
        <form onSubmit={submit} className="space-y-4">
          {children}
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          <div className="flex flex-wrap items-center justify-end gap-2 border-t pt-4">
            {footer}
            <Button type="submit" disabled={busy}>{busy ? "处理中…" : submitLabel}</Button>
          </div>
        </form>
      ) : <div className="space-y-4">{children}</div>}
    </section>
  );
}
