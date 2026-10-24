import { useState } from "react";
import { KeyRound } from "lucide-react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { FormDialog } from "@/components/FormDialog";
import { Panel } from "@/components/Panel";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useConsole } from "@/features/console/context";

const EMPTY = { current: "", password: "", confirm: "" };

/** Change one's own password; the server signs the user out everywhere else. The page says what that does and offers
 * the button; the passwords are typed in a dialog (PRD 2026-10-23: single-field settings use dialogs too). The account
 * menu's 修改密码 opens this page. */
export function PasswordPanel() {
  const { me } = useConsole();
  const [changing, setChanging] = useState(false);
  return (
    <Panel title="修改密码"
      description="修改后，其他设备与客户端上用这个账号登录的会话会退出；Coding Agent 的长期 Token 也会失效，需要重新连接。">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-card px-4 py-3">
        <div className="min-w-0 space-y-0.5">
          <p className="text-sm font-medium">登录密码</p>
          <p className="text-xs text-muted-foreground">{me.username} 登录控制台时使用；新密码至少 12 个字符。</p>
        </div>
        <Button type="button" onClick={() => setChanging(true)}><KeyRound />修改密码</Button>
      </div>
      {changing && <PasswordDialog onClose={(done) => {
        setChanging(false);
        if (done) toast.success("已修改密码");
      }} />}
    </Panel>
  );
}

/** The current password and the new one, twice; the server's reason for a refusal stays in the dialog. */
function PasswordDialog({ onClose }: { onClose: (done?: boolean) => void }) {
  const [form, setForm] = useState(EMPTY);
  const field = (key: keyof typeof form, id: string, label: string) => (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input id={id} type="password" autoComplete={key === "current" ? "current-password" : "new-password"} required
        autoFocus={key === "current"} value={form[key]} onChange={(e) => setForm({ ...form, [key]: e.target.value })} />
    </div>
  );
  return (
    <FormDialog title="修改密码" submitLabel="修改密码" onClose={() => onClose()}
      description="其他设备与客户端上的登录会退出，Coding Agent 的长期 Token 也会失效。"
      onSubmit={async () => {
        if (form.password.length < 12) throw new Error("新密码至少 12 个字符");
        if (form.password !== form.confirm) throw new Error("两次输入不一致");
        await api("/me/password", "POST", { current: form.current, password: form.password });
        onClose(true);
      }}>
      {field("current", "password-current", "原密码")}
      {field("password", "password-new", "新密码")}
      {field("confirm", "password-confirm", "确认新密码")}
    </FormDialog>
  );
}
