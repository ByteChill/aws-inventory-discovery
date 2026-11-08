import {
  type ColumnDef,
  type ColumnSizingState,
  getCoreRowModel,
  useReactTable,
  type VisibilityState,
} from "@tanstack/react-table"
import {
  ArrowDown,
  ArrowUp,
  ChevronLeft,
  ChevronRight,
  Columns3,
  Filter,
  X,
} from "lucide-react"
import { type ReactNode, useMemo } from "react"

import { CopyButton } from "@/components/copy-button"
import { EmptyState, InlineError } from "@/components/inline-state"
import { Time } from "@/components/time"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover"
import { Skeleton } from "@/components/ui/skeleton"
import { useDelayedFlag } from "@/hooks/use-delayed-flag"
import { useLocalStorage } from "@/hooks/use-local-storage"
import { type MessageKey, useI18n } from "@/i18n"
import { errorText } from "@/lib/errors"
import { formatNumber } from "@/lib/format"
import { cn } from "@/lib/utils"
import { describeFilter, FilterControl } from "./filters"
import {
  type Column,
  type FacetBucket,
  type FilterValue,
  PAGE_SIZES,
  type RowFlag,
  type TableState,
} from "./types"

export type Entity = "resources" | "accounts" | "scans" | "tasks"

export type DataTableProps<Row extends object> = {
  /** Namespaces the remembered column visibility and widths. */
  tableId: string
  entity: Entity
  columns: Column[]
  rows: Row[]
  total: number
  /** Table state, normally from the route search params (useTableUrlState). */
  state: TableState
  onStateChange: (next: TableState) => void
  /** Option counts for enum filters, keyed by column key. */
  facets?: Record<string, FacetBucket[]>
  isLoading?: boolean
  error?: unknown
  onRetry?: () => void
  getRowId?: (row: Row, index: number) => string
  /** Defaults to row[key], then row.attrs[key]. */
  getCellValue?: (row: Row, key: string) => unknown
  /** Return a node to replace the default cell (links, custom formats). */
  renderCell?: (column: Column, row: Row) => ReactNode | undefined
  /** Abnormal states to badge next to the first column. */
  getRowFlags?: (row: Row) => RowFlag[]
  /** Labels for chips of filters that are not columns (sidebar filters). */
  filterLabels?: Record<string, string>
  toolbarStart?: ReactNode
  toolbarEnd?: ReactNode
}

const DEFAULT_WIDTH: Record<Column["type"], number> = {
  text: 180,
  enum: 140,
  number: 110,
  bool: 90,
  datetime: 140,
}

function defaultValue(row: object, key: string): unknown {
  const r = row as Record<string, unknown>
  if (key in r) return r[key]
  const attrs = r.attrs as Record<string, unknown> | undefined
  return attrs?.[key]
}

const FLAG_TONE: Record<RowFlag, "warn" | "neutral"> = {
  missing: "warn",
  deleted: "neutral",
  stale: "neutral",
}

export function DataTable<Row extends object>({
  tableId,
  entity,
  columns,
  rows,
  total,
  state,
  onStateChange,
  facets,
  isLoading = false,
  error,
  onRetry,
  getRowId,
  getCellValue = defaultValue,
  renderCell,
  getRowFlags,
  filterLabels,
  toolbarStart,
  toolbarEnd,
}: DataTableProps<Row>) {
  const { t, locale } = useI18n()
  const [visibility, setVisibility] = useLocalStorage<VisibilityState>(
    `table:${tableId}:visibility`,
    {},
  )
  const [sizing, setSizing] = useLocalStorage<ColumnSizingState>(
    `table:${tableId}:sizing`,
    {},
  )

  const defs = useMemo<ColumnDef<Row>[]>(
    () =>
      columns.map((c) => ({
        id: c.key,
        header: c.label,
        accessorFn: (row) => getCellValue(row, c.key),
        size: c.mono ? 260 : DEFAULT_WIDTH[c.type],
        minSize: 80,
        meta: c,
      })),
    [columns, getCellValue],
  )

  const table = useReactTable({
    data: rows,
    columns: defs,
    getCoreRowModel: getCoreRowModel(),
    getRowId,
    manualSorting: true,
    manualFiltering: true,
    manualPagination: true,
    enableColumnResizing: true,
    columnResizeMode: "onChange",
    state: { columnVisibility: visibility, columnSizing: sizing },
    onColumnVisibilityChange: setVisibility,
    onColumnSizingChange: setSizing,
  })

  const showSkeleton = useDelayedFlag(isLoading && rows.length === 0)
  const filterEntries = Object.entries(state.filters)
  const hasFilters = filterEntries.length > 0 || !!state.q
  const pages = Math.max(1, Math.ceil(total / state.pageSize))

  function update(patch: Partial<TableState>) {
    onStateChange({ ...state, ...patch })
  }
  function setFilter(key: string, value: FilterValue | undefined) {
    const filters = { ...state.filters }
    if (value === undefined) delete filters[key]
    else filters[key] = value
    update({ filters, page: 1 })
  }
  function toggleSort(key: string) {
    const current = state.sort?.replace(/^-/, "")
    const desc = state.sort?.startsWith("-")
    let sort: string | undefined = key
    if (current === key) sort = desc ? undefined : `-${key}`
    update({ sort, page: 1 })
  }

  const columnByKey = new Map(columns.map((c) => [c.key, c]))
  const visibleHeaders = table.getHeaderGroups()[0]?.headers ?? []

  let body: ReactNode = null
  if (error) {
    body = (
      <div className="px-6 py-8">
        <InlineError message={errorText(t, error)} onRetry={onRetry} />
      </div>
    )
  } else if (!isLoading && rows.length === 0) {
    body = (
      <EmptyState
        message={t(
          `table.${hasFilters ? "empty" : "emptyUnfiltered"}.${entity}` as MessageKey,
        )}
        action={
          hasFilters ? (
            <Button
              variant="outline"
              onClick={() => update({ filters: {}, q: undefined, page: 1 })}
            >
              {t("action.clearFilters")}
            </Button>
          ) : undefined
        }
      />
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 pb-3">
        {toolbarStart}
        {filterEntries.length > 0 && (
          <ul
            aria-label={t("table.activeFilters")}
            className="flex flex-wrap items-center gap-1.5"
          >
            {filterEntries.map(([key, value]) => {
              const col = columnByKey.get(key)
              const label = col?.label ?? filterLabels?.[key] ?? key
              return (
                <li
                  key={key}
                  className="inline-flex min-h-6 items-center gap-1 rounded-md bg-subtle-strong py-0.5 pr-1 pl-2 text-xs"
                >
                  <span className="break-words">
                    {t("table.chip", {
                      label,
                      value: describeFilter(
                        col,
                        value,
                        facets?.[key],
                        t,
                        locale,
                      ),
                    })}
                  </span>
                  <Button
                    variant="ghost"
                    size="icon"
                    className="size-5"
                    aria-label={`${t("action.remove")} ${label}`}
                    onClick={() => setFilter(key, undefined)}
                  >
                    <X className="size-3.5" />
                  </Button>
                </li>
              )
            })}
            <li>
              <Button
                variant="link"
                size="sm"
                onClick={() => update({ filters: {}, page: 1 })}
              >
                {t("action.clearAll")}
              </Button>
            </li>
          </ul>
        )}
        <div className="ml-auto flex items-center gap-2">
          {toolbarEnd}
          {columns.length > 0 && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline">
                  <Columns3 />
                  {t("table.columns")}
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent className="max-h-80 overflow-auto">
                {table.getAllLeafColumns().map((col) => (
                  <DropdownMenuCheckboxItem
                    key={col.id}
                    checked={col.getIsVisible()}
                    onCheckedChange={(v) => col.toggleVisibility(!!v)}
                    onSelect={(e) => e.preventDefault()}
                  >
                    {columnByKey.get(col.id)?.label ?? col.id}
                  </DropdownMenuCheckboxItem>
                ))}
                <DropdownMenuSeparator />
                <DropdownMenuItem
                  onSelect={() => {
                    setVisibility({})
                    setSizing({})
                  }}
                >
                  {t("table.resetColumns")}
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          )}
        </div>
      </div>

      <div
        className="min-h-0 flex-1 overflow-auto rounded-lg border bg-card"
        aria-busy={isLoading}
      >
        {columns.length > 0 && (
          <table
            className="border-separate border-spacing-0 text-left"
            style={{
              width: `max(100%, ${table.getTotalSize()}px)`,
              tableLayout: "fixed",
            }}
          >
            <thead>
              <tr>
                {visibleHeaders.map((header) => {
                  const col = header.column.columnDef.meta as Column
                  const sorted =
                    state.sort?.replace(/^-/, "") === col.key
                      ? state.sort?.startsWith("-")
                        ? "desc"
                        : "asc"
                      : undefined
                  const filterActive = col.key in state.filters
                  return (
                    <th
                      key={header.id}
                      scope="col"
                      aria-sort={
                        sorted === "asc"
                          ? "ascending"
                          : sorted === "desc"
                            ? "descending"
                            : undefined
                      }
                      style={{ width: header.getSize() }}
                      className="sticky top-0 z-10 border-b bg-card px-2 py-1.5 align-top font-medium"
                    >
                      <div
                        className={cn(
                          "flex items-start gap-1",
                          col.type === "number" && "justify-end",
                        )}
                      >
                        {col.sortable ? (
                          <button
                            type="button"
                            onClick={() => toggleSort(col.key)}
                            aria-label={t("table.sortBy", {
                              column: col.label,
                            })}
                            className="inline-flex items-start gap-1 text-left font-medium hover:text-primary"
                          >
                            <span className="break-words">{col.label}</span>
                            {sorted === "asc" && (
                              <ArrowUp className="mt-0.5 size-3.5 shrink-0 text-primary" />
                            )}
                            {sorted === "desc" && (
                              <ArrowDown className="mt-0.5 size-3.5 shrink-0 text-primary" />
                            )}
                          </button>
                        ) : (
                          <span className="break-words">{col.label}</span>
                        )}
                        {col.filterable && (
                          <Popover>
                            <PopoverTrigger asChild>
                              <Button
                                variant="ghost"
                                size="icon"
                                className={cn(
                                  "size-5 shrink-0",
                                  filterActive
                                    ? "text-primary"
                                    : "text-muted-foreground",
                                )}
                                aria-label={t("table.filterBy", {
                                  column: col.label,
                                })}
                              >
                                <Filter className="size-3.5" />
                              </Button>
                            </PopoverTrigger>
                            <PopoverContent className="w-60">
                              <FilterControl
                                column={col}
                                value={state.filters[col.key]}
                                options={facets?.[col.key]}
                                onChange={(v) => setFilter(col.key, v)}
                              />
                            </PopoverContent>
                          </Popover>
                        )}
                      </div>
                      {/* biome-ignore lint/a11y/useSemanticElements: a resize handle is not a form separator */}
                      <div
                        role="separator"
                        aria-orientation="vertical"
                        aria-label={t("table.resize", { column: col.label })}
                        onMouseDown={header.getResizeHandler()}
                        onTouchStart={header.getResizeHandler()}
                        onDoubleClick={() => header.column.resetSize()}
                        className={cn(
                          "absolute top-0 right-0 h-full w-1.5 cursor-col-resize touch-none select-none hover:bg-primary/30",
                          header.column.getIsResizing() && "bg-primary/50",
                        )}
                      />
                    </th>
                  )
                })}
              </tr>
            </thead>
            <tbody>
              {showSkeleton &&
                Array.from({ length: 8 }, (_, r) => (
                  // biome-ignore lint/suspicious/noArrayIndexKey: static placeholders
                  <tr key={r}>
                    {visibleHeaders.map((h) => (
                      <td key={h.id} className="border-b px-2 py-2.5">
                        <Skeleton className="h-4 w-3/4" />
                      </td>
                    ))}
                  </tr>
                ))}
              {!error &&
                table.getRowModel().rows.map((row) => {
                  const flags = getRowFlags?.(row.original) ?? []
                  return (
                    <tr key={row.id} className="group hover:bg-subtle">
                      {row.getVisibleCells().map((cell, i) => {
                        const col = cell.column.columnDef.meta as Column
                        return (
                          <td
                            key={cell.id}
                            style={{ width: cell.column.getSize() }}
                            className={cn(
                              "border-b px-2 py-1.5 align-top break-words",
                              col.type === "number" && "text-right",
                            )}
                          >
                            <CellContent
                              column={col}
                              value={cell.getValue()}
                              custom={renderCell?.(col, row.original)}
                              options={facets?.[col.key]}
                            />
                            {i === 0 && flags.length > 0 && (
                              <span className="ml-1.5 inline-flex gap-1 align-middle">
                                {flags.map((f) => (
                                  <Badge key={f} tone={FLAG_TONE[f]}>
                                    {t(`status.${f}`)}
                                  </Badge>
                                ))}
                              </span>
                            )}
                          </td>
                        )
                      })}
                    </tr>
                  )
                })}
            </tbody>
          </table>
        )}
        {body}
      </div>

      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 pt-3">
        <p>
          {total > 0 || !isLoading
            ? t(`table.total.${entity}` as MessageKey, { count: total })
            : ""}
        </p>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-2 text-muted-foreground">
            {t("table.pageSize")}
            <select
              value={state.pageSize}
              onChange={(e) =>
                update({ pageSize: Number(e.target.value), page: 1 })
              }
              className="h-8 rounded-md border border-input bg-card px-2 text-foreground"
            >
              {PAGE_SIZES.map((n) => (
                <option key={n} value={n}>
                  {formatNumber(n, locale)}
                </option>
              ))}
            </select>
          </label>
          <span className="text-muted-foreground">
            {t("table.page", { page: state.page, pages })}
          </span>
          <div className="flex gap-1">
            <Button
              variant="outline"
              size="icon"
              aria-label={t("table.prev")}
              disabled={state.page <= 1}
              onClick={() => update({ page: state.page - 1 })}
            >
              <ChevronLeft />
            </Button>
            <Button
              variant="outline"
              size="icon"
              aria-label={t("table.next")}
              disabled={state.page >= pages}
              onClick={() => update({ page: state.page + 1 })}
            >
              <ChevronRight />
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}

function CellContent({
  column,
  value,
  custom,
  options,
}: {
  column: Column
  value: unknown
  custom: ReactNode | undefined
  options: FacetBucket[] | undefined
}) {
  const { t, locale } = useI18n()
  if (custom !== undefined) return <>{custom}</>
  if (value === null || value === undefined || value === "") return null

  switch (column.type) {
    case "number":
      return <>{formatNumber(Number(value), locale)}</>
    case "bool":
      return <>{value ? t("common.yes") : t("common.no")}</>
    case "datetime":
      return <Time value={String(value)} />
    case "enum": {
      const text = String(value)
      return <>{options?.find((o) => o.value === text)?.label ?? text}</>
    }
    default:
      if (column.mono) {
        const text = String(value)
        return (
          <span className="inline-flex items-start gap-0.5">
            <span className="font-mono break-all">{text}</span>
            <CopyButton
              value={text}
              className="opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
            />
          </span>
        )
      }
      return <>{String(value)}</>
  }
}
