import { useState, type FormEvent, type ReactNode } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import { cn } from "@/lib/utils";

interface Props {
  title: string;
  kicker?: string;
  description?: ReactNode;
  children?: ReactNode;
  submitLabel?: string;
  destructive?: boolean;
  note?: ReactNode;
  wide?: boolean;
  onSubmit?: () => Promise<void> | void;
  onClose: () => void;
  footer?: ReactNode;
  /** The closing button's words (「我已保存」 after a one-time secret); 取消 / 关闭 by default. */
  closeLabel?: string;
}

/** Modal form: shows the server's reason on failure and cannot be dismissed while a request is in flight. Closing puts
 * the focus back where it was when the dialog opened (the button that opened it): Radix only returns it to a
 * DialogTrigger, and these dialogs are opened from code, so it fell to the top of the page. */
export function FormDialog({ title, kicker, description, children, submitLabel = "保存", destructive, note, wide, onSubmit, onClose, footer, closeLabel }: Props) {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [returnTo] = useState(() => (typeof document !== "undefined" ? document.activeElement : null) as HTMLElement | null);

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
    <Dialog open onOpenChange={(open) => !open && !busy && onClose()}>
      <DialogContent className={cn("max-h-[92svh] gap-0 overflow-hidden p-0", wide ? "sm:max-w-4xl" : "sm:max-w-xl")}
        onCloseAutoFocus={(event) => {
          if (!returnTo?.isConnected) return;   // a removed row's button is gone: Radix's default then
          event.preventDefault();
          returnTo.focus();
        }}>
        <form onSubmit={submit} className="flex max-h-[92svh] flex-col">
          <DialogHeader className="border-b px-6 pt-6 pb-4 text-left">
            {kicker && <p className="text-[11px] font-semibold tracking-[0.18em] text-muted-foreground">{kicker}</p>}
            <DialogTitle className="text-xl">{title}</DialogTitle>
            {description ? <DialogDescription>{description}</DialogDescription> : <DialogDescription className="sr-only">{title}</DialogDescription>}
          </DialogHeader>
          {children && <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-6 py-5">{children}</div>}
          {error && <p role="alert" className="px-6 pb-2 text-sm text-destructive">{error}</p>}
          <DialogFooter className="items-center gap-2 border-t bg-muted/40 px-6 py-4 sm:justify-between">
            <span className="text-xs text-muted-foreground">{note}</span>
            <div className="flex gap-2">
              {footer}
              <Button type="button" variant="outline" onClick={onClose} disabled={busy}>{closeLabel ?? (onSubmit ? "取消" : "关闭")}</Button>
              {onSubmit && (
                <Button type="submit" variant={destructive ? "destructive" : "default"} disabled={busy}>
                  {busy ? "处理中…" : submitLabel}
                </Button>
              )}
            </div>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
