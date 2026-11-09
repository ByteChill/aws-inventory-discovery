import type { Column, FacetBucket } from "@/client"

// Column, FacetBucket and the paged list shapes come from the generated client.
export type { Column, FacetBucket }
export type ColumnType = Column["type"]

/** An option in a filter list. The count is left out when the API has none. */
export type Option = Omit<FacetBucket, "count"> & { count?: number }

export type Page<T> = {
  items: T[]
  page: number
  pageSize: number
  total: number
}

export type Range<T extends string | number> = { gte?: T; lte?: T }

/**
 * text: contains string, enum: selected values, bool: "true" | "false",
 * number / datetime: inclusive range (datetime values are ISO strings).
 */
export type FilterValue = string | string[] | Range<string | number>

export type TableState = {
  page: number
  pageSize: number
  /** API `sort` format: comma separated keys, "-" prefix for descending. */
  sort: string | undefined
  /** Keyed by column key (or by facet field for sidebar filters). */
  filters: Record<string, FilterValue>
  /** Free-text search. */
  q: string | undefined
}

export const DEFAULT_PAGE_SIZE = 50
export const PAGE_SIZES = [50, 100, 200]

export const emptyTableState: TableState = {
  page: 1,
  pageSize: DEFAULT_PAGE_SIZE,
  sort: undefined,
  filters: {},
  q: undefined,
}

/** Abnormal states only; normal rows get no badge. */
export type RowFlag = "missing" | "deleted" | "stale"
