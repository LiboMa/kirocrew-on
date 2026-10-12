import type { Me } from "@/api/types";

/** Platform administration needs an admin account and a login or token that carries the admin scope (A3). */
export function elevated(me: Pick<Me, "admin" | "scopes">): boolean {
  return !!me.admin && me.scopes.includes("admin");
}
