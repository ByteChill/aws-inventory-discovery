import type { TableState } from "@/components/data-table"

/** Fields the API takes as plain parameters (DESIGN.md 8.2). */
export const DIRECT_FILTERS = [
  "accountId",
  "eimId",
  "baId",
  "accountType",
  "awsRegion",
  "resourceType",
  "status",
] as const

/**
 * Table state to the API's filter parameters: direct fields as-is (repeated for
 * multiple values), everything else as `attr.<key>`, ranges as `.gte` / `.lte`.
 * Used by the list query (later) and by the export links.
 */
export function resourceQuery(
  state: TableState,
  extra: Record<string, string> = {},
): URLSearchParams {
  const p = new URLSearchParams(extra)
  if (state.q) p.set("q", state.q)
  if (state.sort) p.set("sort", state.sort)
  for (const [key, value] of Object.entries(state.filters)) {
    const name = (DIRECT_FILTERS as readonly string[]).includes(key)
      ? key
      : `attr.${key}`
    if (Array.isArray(value)) {
      for (const v of value) p.append(name, v)
    } else if (typeof value === "object") {
      if (value.gte !== undefined) p.set(`${name}.gte`, String(value.gte))
      if (value.lte !== undefined) p.set(`${name}.lte`, String(value.lte))
    } else {
      p.set(name, value)
    }
  }
  return p
}
