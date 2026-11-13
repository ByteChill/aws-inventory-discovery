import { useNavigate } from "@tanstack/react-router"
import { z } from "zod"

import { scopeParam } from "@/lib/scope"
import { DEFAULT_PAGE_SIZE, type FilterValue, type TableState } from "./types"

const scalar = z.union([z.string(), z.number()])

const filterValue = z.union([
  z.string(),
  z.number().transform(String),
  z.array(scalar.transform(String)),
  z.object({ gte: scalar.optional(), lte: scalar.optional() }),
])

/**
 * Search params shared by every table page. Spread into the route's own schema:
 * `validateSearch: tableSearchSchema.extend({ ... })`.
 * Invalid values fall back to defaults instead of breaking the page.
 */
export const tableSearchSchema = z.object({
  page: z.number().int().min(1).catch(1).optional(),
  pageSize: z
    .number()
    .int()
    .min(1)
    .max(200)
    .catch(DEFAULT_PAGE_SIZE)
    .optional(),
  sort: z.string().optional().catch(undefined),
  q: z.string().optional().catch(undefined),
  f: z.record(z.string(), filterValue).optional().catch(undefined),
  /** Resource browsing scope (`app:<EIM>` / `acct:<id>`); see lib/scope.ts. */
  s: scopeParam,
  /** Show the inventory inside one scan's scope instead of everything. */
  scanRequestId: z.number().int().positive().optional().catch(undefined),
})

export type TableSearch = z.infer<typeof tableSearchSchema>

export function searchToState(search: TableSearch): TableState {
  return {
    page: search.page ?? 1,
    pageSize: search.pageSize ?? DEFAULT_PAGE_SIZE,
    sort: search.sort,
    q: search.q,
    filters: (search.f ?? {}) as Record<string, FilterValue>,
  }
}

/** Defaults are left out so shared URLs stay short. */
export function stateToSearch(state: TableState): TableSearch {
  const hasFilters = Object.keys(state.filters).length > 0
  return {
    page: state.page > 1 ? state.page : undefined,
    pageSize: state.pageSize !== DEFAULT_PAGE_SIZE ? state.pageSize : undefined,
    sort: state.sort || undefined,
    q: state.q || undefined,
    f: hasFilters ? state.filters : undefined,
  }
}

/**
 * Connects a table to the current route's search params:
 *
 *   const { state, setState } = useTableUrlState(Route.useSearch())
 */
export function useTableUrlState(search: TableSearch) {
  // The router is typed per route; the table schema is a subset of every table
  // page's search, so merge into whatever is there.
  const navigate = useNavigate() as unknown as (opts: {
    search: (prev: Record<string, unknown>) => Record<string, unknown>
    replace?: boolean
    resetScroll?: boolean
  }) => void
  const state = searchToState(search)
  return {
    state,
    setState(next: TableState) {
      navigate({
        search: (prev) => ({ ...prev, ...stateToSearch(next) }),
        replace: true,
        // A filter or page change keeps the reader where they were: the table
        // updates in place, and jumping to the top hides the control just used.
        resetScroll: false,
      })
    },
  }
}
