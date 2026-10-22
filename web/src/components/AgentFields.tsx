import { useId } from "react";
import type { Step, Tool } from "@/api/types";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/NativeSelect";
import { EFFORTS, TOOLS } from "@/lib/labels";

type Agent = Pick<Step, "tool" | "model" | "effort">;

/** Coding tool, model (the models this environment published for the tool, chosen from a list) and effort; OpenCode does
 * not take an effort override. A model the environment has not published is kept and marked: dispatch would refuse it. */
export function AgentFields({ value, onChange, models, defaults, idPrefix }: {
  value: Agent; onChange: (next: Agent) => void; models?: Partial<Record<Tool, string[]>>;
  defaults?: Partial<Record<Tool, string>>; idPrefix?: string;
}) {
  const generated = useId();
  const prefix = idPrefix ?? generated;
  // "default" is the tool's own default: the Worker always takes it, as it takes "auto"
  const published = [...new Set(["auto", ...(models?.[value.tool] ?? []), ...(value.model === "default" ? ["default"] : [])])];
  const known = published.includes(value.model);
  const configured = defaults?.[value.tool];   // D51: what "auto" runs where the tool publishes no list
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
        <NativeSelect id={prefix + "-model"} value={value.model} required onChange={(e) => onChange({ ...value, model: e.target.value })}>
          {published.map((model) => <option key={model} value={model}>{model === "auto" ? `自动（${configured ?? "工具默认"}）` : model}</option>)}
          {!known && value.model && <option value={value.model}>{value.model}（此执行环境未公布）</option>}
        </NativeSelect>
        {published.length === 1 && (
          <p className="text-[11px] text-muted-foreground">
            {value.tool === "codex" || configured
              ? `${TOOLS[value.tool] ?? value.tool} 不公布模型列表：「自动」即此执行环境配置的${configured ? " " + configured : "模型"}。`
              : `此执行环境尚未公布 ${TOOLS[value.tool] ?? value.tool} 的模型（平台会自动尝试发现）：可保持自动，或在需求中写明模型，或换到已有模型的执行环境。`}
          </p>
        )}
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
