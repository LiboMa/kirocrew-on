import type { Dialog } from "@/features/console/context";
import { LaunchWizard } from "@/features/launch/LaunchWizard";
import { AssignWorkerDialog, ContinueDialog, ControlDialog } from "@/features/sessions/dialogs";
import { TeamDialog } from "@/features/settings/TeamDialog";
import { TokensDialog } from "@/features/settings/TokensDialog";
import { WorkersDialog } from "@/features/settings/WorkersDialog";
import { ShareDialog } from "@/features/workflows/ShareDialog";
import { WorkflowEditor } from "@/features/workflows/WorkflowEditor";

/** Renders the dialog requested through the console context. */
export function DialogHost({ dialog }: { dialog: Dialog | null }) {
  if (!dialog) return null;
  switch (dialog.kind) {
    case "launch":
      return <LaunchWizard seed={dialog.seed} />;
    case "workflow":
      return <WorkflowEditor key={dialog.flow?.id ?? "new"} flow={dialog.flow} template={dialog.template} />;
    case "share":
      return <ShareDialog flowId={dialog.flowId} />;
    case "team":
      return <TeamDialog />;
    case "tokens":
      return <TokensDialog />;
    case "workers":
      return <WorkersDialog />;
    case "continue":
      return <ContinueDialog run={dialog.run} taskId={dialog.taskId} />;
    case "assign":
      return <AssignWorkerDialog run={dialog.run} />;
    case "control":
      return <ControlDialog run={dialog.run} action={dialog.action} />;
    default:
      return null;
  }
}
