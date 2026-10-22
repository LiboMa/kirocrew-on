import type { Credential, CredentialBinding, CredentialKind, CredentialOwner, CredentialScope } from "@/api/types";

export const KIND_LABELS: Record<CredentialKind, string> = { github_pat: "GitHub Token", gitlab_token: "GitLab Token" };
export const SCOPE_LABELS: Record<CredentialScope, string> = { user: "个人", team: "Team", platform: "平台" };

/** Whose credential it is, as a phrase: 「bob 的个人」「Team「平台组」的」「平台」. */
export function ownerPhrase(scope: CredentialScope, owner: CredentialOwner): string {
  if (scope === "user") return `${owner?.username ?? "某用户"} 的个人`;
  if (scope === "team") return `Team「${owner?.name ?? "未知"}」的`;
  return "平台";
}

/** 「bob 的个人 GitHub Token（…1a2b）」 (D35 §1). */
export function bindingLabel(source: Pick<CredentialBinding, "scope" | "owner" | "kind" | "hint">): string {
  const phrase = ownerPhrase(source.scope, source.owner);
  return `${phrase} ${KIND_LABELS[source.kind] ?? source.kind}（${source.hint}）`;
}

/** The host of an HTTPS repository URL, or "" (credentials are for HTTPS only). */
export function httpsHost(url: string): string {
  try {
    const parsed = new URL(url.trim());
    return parsed.protocol === "https:" ? parsed.host.toLowerCase() : "";
  } catch {
    return "";
  }
}

/** A credential a Session in ``space`` ("" for personal) may use, as the server checks it (D35 R1; D48). */
export function usableIn(c: Credential, space: string): boolean {
  return !c.disabled && (c.scope === "user" || c.scope === "platform" || (c.scope === "team" && c.owner?.id === space));
}

/** A credential as a picker shows it: name, scope, host, the repositories it covers and its hint. */
export function credentialLabel(c: Credential): string {
  return [c.name, SCOPE_LABELS[c.scope], c.host, c.repos?.length ? c.repos.join("、") : "全部仓库", c.hint].join(" · ");
}
