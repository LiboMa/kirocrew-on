import { useState, type FormEvent } from "react";
import { ArrowRight } from "lucide-react";
import { api, setToken } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { TOOLS } from "@/lib/labels";

export function LoginPage() {
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      const result = await api<{ token: string }>("/login", "POST", {
        username: form.get("username"),
        password: form.get("password"),
      });
      setToken(result.token);
    } catch (reason) {
      setError((reason as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="grid min-h-svh lg:grid-cols-[1.15fr_1fr]">
      <section className="hidden flex-col justify-between bg-gradient-to-br from-primary/10 via-background to-primary/5 p-14 lg:flex">
        <Brand />
        <div className="space-y-7">
          <p className="text-xs font-semibold tracking-[0.2em] text-muted-foreground">TEAM DEVELOPMENT WORKSPACE</p>
          <h1 className="text-5xl leading-tight font-bold tracking-tight text-foreground">
            一个需求。<br />多种 Coding Agent。<br />同一条开发脉络。
          </h1>
          <p className="max-w-md leading-7 text-muted-foreground">
            把规划、编码和审查连在一起。保留源码、交接与执行证据，让下一次开发从这里继续。
          </p>
          <div className="flex flex-wrap items-center gap-2 text-sm">
            {Object.values(TOOLS).map((name, index) => (
              <span key={name} className="flex items-center gap-2">
                {index > 0 && <ArrowRight className="size-3.5 text-muted-foreground" aria-hidden />}
                <span className="rounded-md border bg-card px-3 py-1.5 shadow-xs">{name}</span>
              </span>
            ))}
          </div>
        </div>
        <p className="text-[11px] tracking-widest text-muted-foreground">POWERED BY KIROCREW ACP · SQLITE · TEAM API</p>
      </section>
      <section className="flex items-center justify-center p-8">
        <form onSubmit={submit} className="w-full max-w-sm space-y-6" aria-label="登录">
          <div className="lg:hidden"><Brand /></div>
          <div className="space-y-2">
            <p className="text-xs font-semibold tracking-[0.2em] text-muted-foreground">WELCOME BACK</p>
            <h2 className="text-3xl font-bold tracking-tight">进入开发空间</h2>
            <p className="text-sm text-muted-foreground">使用平台账户登录。工作流默认仅自己可见。</p>
          </div>
          <div className="space-y-2">
            <Label htmlFor="login-username">用户名</Label>
            <Input id="login-username" name="username" autoComplete="username" placeholder="your.name" required />
          </div>
          <div className="space-y-2">
            <Label htmlFor="login-password">密码</Label>
            <Input id="login-password" name="password" type="password" autoComplete="current-password" required />
          </div>
          {error && <p role="alert" className="text-sm text-destructive">{error}</p>}
          <Button type="submit" className="w-full justify-between" size="lg" disabled={busy}>
            {busy ? "正在登录…" : "登录工作台"} <ArrowRight aria-hidden />
          </Button>
          <p className="text-xs leading-6 text-muted-foreground">
            首次使用：由管理员初始化账户。原有 KiroCrew 对话与本版账户分别管理。
            <a className="ml-1 text-primary underline-offset-4 hover:underline" href="/classic/">打开经典控制台</a>
          </p>
        </form>
      </section>
    </main>
  );
}

export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className="flex shrink-0 items-center gap-3">
      <span className="grid size-9 place-items-center rounded-lg bg-primary text-lg font-bold text-primary-foreground">K</span>
      <span className={compact ? "hidden leading-tight sm:block" : "leading-tight"}>
        <strong className="block text-sm tracking-wide">KiroCrew ON</strong>
        <small className="text-[11px] text-muted-foreground">开发工作台</small>
      </span>
    </div>
  );
}
