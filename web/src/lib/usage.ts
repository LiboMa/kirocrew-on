/** D46 P6a: what an Attempt or a Session used, in one line — the same words as octopus/usage.py `line`. */
export interface Usage {
  credits?: number; input_tokens?: number; output_tokens?: number; cache_read_tokens?: number; cache_write_tokens?: number;
  cost_usd?: number; seconds?: number; turns?: number; model?: string; tokens?: "unavailable";
  attempts?: number; tokens_unavailable?: number;
}

function count(n: number): string {
  const short = (value: number) => value.toFixed(1).replace(/\.0$/, "");
  return n >= 1_000_000 ? short(n / 1_000_000) + "M" : n >= 1000 ? short(n / 1000) + "k" : String(n);
}

function duration(seconds: number): string {
  const whole = Math.floor(seconds + 0.5);
  return whole >= 60 ? `${Math.floor(whole / 60)} 分 ${whole % 60} 秒` : `${whole} 秒`;
}

export function usageLine(usage: Usage | null | undefined): string {
  const u = usage ?? {};
  const parts: string[] = [];
  if (u.credits) parts.push(`${Number(u.credits.toPrecision(6))} credits`);
  if (u.input_tokens || u.output_tokens) parts.push(`输入 ${count(u.input_tokens ?? 0)} / 输出 ${count(u.output_tokens ?? 0)} token`);
  if (u.cost_usd) parts.push(`$${u.cost_usd.toFixed(2)}`);
  if (typeof u.seconds === "number") parts.push(duration(u.seconds));
  return parts.join(" · ") || "—";
}
