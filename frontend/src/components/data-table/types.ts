// Shapes from docs/specs/api.md section 1 and 2. Replace with the generated
// types in src/client once the API publishes them.

export type ColumnType = "text" | "enum" | "number" | "bool" | "datetime"

export type Column = {
  key: string
  label: string
  type: ColumnType
  mono: boolean
  sortable: boolean
  filterable: boolean
  scope: "common" | "type"
}

export type FacetBucket = { value: string; label: string; count: number }

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
