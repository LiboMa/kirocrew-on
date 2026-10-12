import type { RecoveryPolicy } from "@/api/types";
import { Checkbox } from "@/components/ui/checkbox";
import { Label } from "@/components/ui/label";
import { NativeSelect } from "@/components/NativeSelect";

export const DEFAULT_RECOVERY: RecoveryPolicy = { enabled: true, max_cycles: 2 };

export function RecoveryFields({ value, onChange, idPrefix = "recovery" }: {
  value: RecoveryPolicy; onChange: (next: RecoveryPolicy) => void; idPrefix?: string;
}) {
  return (
    <div className="space-y-3 rounded-lg border bg-primary/5 p-4">
      <div className="flex items-center gap-2.5">
        <Checkbox id={idPrefix + "-enabled"} checked={value.enabled}
          onCheckedChange={(checked) => onChange({ ...value, enabled: checked === true })} />
        <Label htmlFor={idPrefix + "-enabled"}>失败时由 Coordinator 自动诊断并安排修复</Label>
      </div>
      <div className="max-w-40 space-y-1.5">
        <Label htmlFor={idPrefix + "-limit"}>最多自动纠错轮次</Label>
        <NativeSelect id={idPrefix + "-limit"} value={value.max_cycles}
          onChange={(e) => onChange({ ...value, max_cycles: Number(e.target.value) })}>
          {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n} 轮</option>)}
        </NativeSelect>
      </div>
      <p className="text-xs leading-6 text-muted-foreground">同一工作区内返工，保留历史并重新审查。缺少项目输入、认证或权限时说明原因，等待处理。</p>
    </div>
  );
}
