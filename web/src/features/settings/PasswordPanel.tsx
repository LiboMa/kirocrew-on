import { useState } from "react";
import { toast } from "sonner";
import { api } from "@/api/client";
import { Panel } from "@/components/Panel";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

const EMPTY = { current: "", password: "", confirm: "" };

/** Change one's own password; the server signs the user out everywhere else. */
export function PasswordPanel() {
  const [form, setForm] = useState(EMPTY);
  const field = (key: keyof typeof form, id: string, label: string) => (
    <div className="space-y-1.5">
      <Label htmlFor={id}>{label}</Label>
      <Input id={id} type="password" autoComplete={key === "current" ? "current-password" : "new-password"} required
        value={form[key]} onChange={(e) => setForm({ ...form, [key]: e.target.value })} />
    </div>
  );
  return (
    <Panel title="修改密码" submitLabel="修改密码"
      description="修改后，其他设备与客户端上用这个账号登录的会话会退出；Coding Agent 的长期 Token 也会失效，需要重新连接。"
      onSubmit={async () => {
        if (form.password.length < 12) throw new Error("新密码至少 12 个字符");
        if (form.password !== form.confirm) throw new Error("两次输入不一致");
        await api("/me/password", "POST", { current: form.current, password: form.password });
        setForm(EMPTY);
        toast.success("密码已修改。");
      }}>
      <div className="grid max-w-md gap-4">
        {field("current", "password-current", "原密码")}
        {field("password", "password-new", "新密码")}
        {field("confirm", "password-confirm", "确认新密码")}
      </div>
    </Panel>
  );
}
