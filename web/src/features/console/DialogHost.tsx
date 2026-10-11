import type { Dialog } from "@/features/console/context";

/** Renders the dialog requested through the console context (filled in as features land). */
export function DialogHost({ dialog }: { dialog: Dialog | null }) {
  if (!dialog) return null;
  return null;
}
