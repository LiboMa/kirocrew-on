import type { Dialog } from "@/features/console/context";
import { LaunchWizard } from "@/features/launch/LaunchWizard";
import { AssignWorkerDialog, ContinueDialog, ControlDialog, TakeoverDialog } from "@/features/sessions/dialogs";
import { RunShareDialog } from "@/features/sessions/RunShareDialog";
import { ShareDialog } from "@/features/workflows/ShareDialog";
import { WorkflowEditor } from "@/features/workflows/WorkflowEditor";

/** Renders the dialog requested through the console context. Settings are pages now (管理, 我的设置), not dialogs. */
export function DialogHost({ dialog }: { dialog: Dialog | null }) {
  if (!dialog) return null;
  switch (dialog.kind) {
    case "launch":
      return <LaunchWizard seed={dialog.seed} />;
    case "workflow":
      return <WorkflowEditor key={dialog.flow?.id ?? "new"} flow={dialog.flow} template={dialog.template} />;
    case "share":
      return <ShareDialog flowId={dialog.flowId} />;
    case "run-share":
      return <RunShareDialog run={dialog.run} />;
    case "continue":
      return <ContinueDialog run={dialog.run} taskId={dialog.taskId} note={dialog.note} />;
    case "assign":
      return <AssignWorkerDialog run={dialog.run} />;
    case "control":
      return <ControlDialog run={dialog.run} action={dialog.action} />;
    case "takeover":
      return <TakeoverDialog run={dialog.run} />;
    default:
      return null;
  }
}
