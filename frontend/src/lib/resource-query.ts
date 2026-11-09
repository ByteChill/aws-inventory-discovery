import type { Column, TableState } from "@/components/data-table"

/** Query parameters as the generated client takes them: arrays are repeated. */
export type Params = Record<string, string | string[]>

/** Fields the API takes as plain parameters (DESIGN.md 8.2). */
export const DIRECT_FILTERS = [
  "accountId",
  "eimId",
  "baId",
  "accountType",
  "awsRegion",
  "resourceType",
  "status",
  "tag",
  "tagMissing",
] as const

/** Filter keys that are not columns but are sent as they are. */
const PASS_THROUGH: readonly string[] = DIRECT_FILTERS

/**
 * The single resource type a state selects, if it selects exactly one. The
 * type-specific columns, their filters and `attr.*` parameters exist only then.
 */
export function singleType(state: TableState): string | undefined {
  const v = state.filters.resourceType
  if (typeof v === "string") return v
  if (Array.isArray(v) && v.length === 1) return v[0]
  return undefined
}

/**
 * Table state to the resource filter parameters: direct fields as they are
 * (repeated for several values), common datetime columns as `<key>.gte` /
 * `.lte`, type columns as `attr.<key>`. Filters the current columns do not know
 * are left out, so a stale filter in a shared URL cannot break the request.
 */
export function resourceParams(
  state: TableState,
  columns: Column[],
  extra: Params = {},
): Params {
  const byKey = new Map(columns.map((c) => [c.key, c]))
  const out: Params = { ...extra }
  if (state.q) out.q = state.q
  for (const [key, value] of Object.entries(state.filters)) {
    let name: string
    if (PASS_THROUGH.includes(key)) name = key
    else if (key === "tags") name = "tag"
    else {
      const column = byKey.get(key)
      if (!column) continue
      name = column.scope === "type" ? `attr.${key}` : key
    }
    if (Array.isArray(value)) {
      if (value.length) out[name] = value
    } else if (typeof value === "object") {
      if (value.gte !== undefined) out[`${name}.gte`] = String(value.gte)
      if (value.lte !== undefined) out[`${name}.lte`] = String(value.lte)
    } else if (value !== "") {
      out[name] = value
    }
  }
  return out
}

export function toSearchParams(params: Params): URLSearchParams {
  const p = new URLSearchParams()
  for (const [key, value] of Object.entries(params)) {
    if (Array.isArray(value)) for (const v of value) p.append(key, v)
    else p.set(key, value)
  }
  return p
}
