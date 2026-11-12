import { z } from "zod"

/**
 * Resource browsing scope, carried in the `s` search parameter:
 * `app:<EIM>` or `acct:<account id>`. Absent means every application and account.
 * The scope only narrows resource browsing; scans and accounts are never scoped.
 */
export type Scope = { kind: "app"; eim: string } | { kind: "acct"; id: string }

export const scopeParam = z.string().optional().catch(undefined)

export function parseScope(value: string | undefined): Scope | null {
  if (!value) return null
  const [kind, id] = value.split(":")
  if (kind === "app" && id) return { kind: "app", eim: id }
  if (kind === "acct" && id) return { kind: "acct", id }
  return null
}

export function scopeValue(scope: Scope | null): string | undefined {
  if (!scope) return undefined
  return scope.kind === "app" ? `app:${scope.eim}` : `acct:${scope.id}`
}

/** Resource filter parameters implied by a scope. */
export function scopeParams(scope: Scope | null): Record<string, string[]> {
  if (!scope) return {}
  return scope.kind === "app"
    ? { eimId: [scope.eim] }
    : { accountId: [scope.id] }
}

/** Carry the scope into a link's search object, leaving defaults out. */
export function withScope(
  scope: Scope | null,
  search: Record<string, unknown> = {},
): Record<string, unknown> {
  const value = scopeValue(scope)
  return value ? { ...search, s: value } : search
}
