import { useRun } from "@/api/hooks";
import { useConsole, type Dialog } from "@/features/console/context";
import { LaunchWizard } from "@/features/launch/LaunchWizard";
import { AssignWorkerDialog, ContinueDialog, ControlDialog } from "@/features/sessions/dialogs";

/** Renders the dialog requested through the console context. Session dialogs act on the selected Session. */
export function DialogHost({ dialog }: { dialog: Dialog | null }) {
  const { selected } = useConsole();
  const run = useRun(selected).data;
  if (!dialog) return null;
  switch (dialog.kind) {
    case "launch":
      return <LaunchWizard seed={dialog.seed} />;
    case "continue":
      return run ? <ContinueDialog key={run.id} run={run} taskId={dialog.taskId} /> : null;
    case "assign":
      return run ? <AssignWorkerDialog run={run} /> : null;
    case "control":
      return run ? <ControlDialog run={run} action={dialog.action} /> : null;
    default:
      return null;
  }
}
