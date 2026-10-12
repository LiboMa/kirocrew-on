import { useId } from "react";
import type { Step, Tool } from "@/api/types";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/NativeSelect";
import { EFFORTS, TOOLS } from "@/lib/labels";

type Agent = Pick<Step, "tool" | "model" | "effort">;

/** Coding tool, model (suggested from the Worker's catalogue) and effort; OpenCode does not take an effort override. */
export function AgentFields({ value, onChange, models, idPrefix }: {
  value: Agent; onChange: (next: Agent) => void; models?: Partial<Record<Tool, string[]>>; idPrefix?: string;
}) {
  const generated = useId();
  const prefix = idPrefix ?? generated;
  const suggestions = models?.[value.tool] ?? ["auto"];
  return (
    <div className="grid gap-3 sm:grid-cols-3">
      <div className="space-y-1.5">
        <Label htmlFor={prefix + "-tool"}>Coding 工具</Label>
        <NativeSelect id={prefix + "-tool"} value={value.tool}
          onChange={(e) => onChange({ tool: e.target.value as Tool, model: "auto", effort: "" })}>
          {Object.entries(TOOLS).map(([tool, label]) => <option key={tool} value={tool}>{label}</option>)}
        </NativeSelect>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor={prefix + "-model"}>模型</Label>
        <Input id={prefix + "-model"} list={prefix + "-models"} value={value.model} required
          onChange={(e) => onChange({ ...value, model: e.target.value })} />
        <datalist id={prefix + "-models"}>{suggestions.map((model) => <option key={model} value={model} />)}</datalist>
      </div>
      <div className="space-y-1.5">
        <Label htmlFor={prefix + "-effort"}>Effort</Label>
        <NativeSelect id={prefix + "-effort"} value={value.tool === "opencode" ? "" : value.effort}
          disabled={value.tool === "opencode"} onChange={(e) => onChange({ ...value, effort: e.target.value })}>
          <option value="">继承默认</option>
          {EFFORTS.map((effort) => <option key={effort} value={effort}>{effort}</option>)}
        </NativeSelect>
      </div>
    </div>
  );
}
