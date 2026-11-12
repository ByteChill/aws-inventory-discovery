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
} from "lucide-react"
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react"

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
import { FilterControl } from "./filters"
import {
  type Column,
  type FilterValue,
  type Option,
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
  facets?: Record<string, Option[]>
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
  /** Column keys hidden until the user shows them. */
  defaultHidden?: string[]
  /** Explicit pixel width per column key; otherwise the column type decides. */
  columnWidths?: Record<string, number>
  /** A filter row above the table (replaces the per-header filter popovers). */
  toolbar?: ReactNode
  toolbarEnd?: ReactNode
  /** Keep the first column in place while the table scrolls sideways. */
  stickyFirst?: boolean
  /** Per-header filter buttons; off when the page has its own filter row. */
  filterPopovers?: boolean
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
  defaultHidden,
  columnWidths,
  toolbar,
  toolbarEnd,
  stickyFirst = false,
  filterPopovers = true,
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

  const hiddenByDefault = useMemo(
    () => Object.fromEntries((defaultHidden ?? []).map((k) => [k, false])),
    [defaultHidden],
  )

  const defs = useMemo<ColumnDef<Row>[]>(
    () =>
      columns.map((c) => ({
        id: c.key,
        header: c.label,
        accessorFn: (row) => getCellValue(row, c.key),
        size: columnWidths?.[c.key] ?? (c.mono ? 260 : DEFAULT_WIDTH[c.type]),
        minSize: 80,
        meta: c,
      })),
    [columns, getCellValue, columnWidths],
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
    state: {
      columnVisibility: { ...hiddenByDefault, ...visibility },
      columnSizing: sizing,
    },
    onColumnVisibilityChange: setVisibility,
    onColumnSizingChange: setSizing,
  })

  const scrollRef = useRef<HTMLDivElement>(null)
  const [edges, setEdges] = useState({ scrolled: false, more: false })

  // Re-measure after each page of rows: the overflow depends on content, not just the box.
  // biome-ignore lint/correctness/useExhaustiveDependencies: rows and columns change the table width
  useEffect(() => {
    const node = scrollRef.current
    if (!node) return
    const measure = () => {
      setEdges({
        scrolled: node.scrollLeft > 0,
        more: node.scrollLeft + node.clientWidth < node.scrollWidth - 1,
      })
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(node)
    return () => observer.disconnect()
  }, [columns.length, rows.length])

  const showSkeleton = useDelayedFlag(isLoading && rows.length === 0)
  const hasFilters = Object.keys(state.filters).length > 0 || !!state.q
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
      <div className="flex flex-wrap items-start gap-x-3 gap-y-2 pb-3">
        <div className="min-w-0 flex-1">{toolbar}</div>
        <div className="ml-auto flex items-center gap-2">
          {toolbarEnd}
          {columns.length > 0 && (
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" aria-label={t("table.columns")}>
                  <Columns3 />
                  <span className="max-[800px]:hidden">
                    {t("table.columns")}
                  </span>
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

      <div className="relative min-h-0 flex-1">
        {columns.length > 0 && !body && (
          <div
            ref={scrollRef}
            onScroll={() => {
              const node = scrollRef.current
              if (!node) return
              setEdges({
                scrolled: node.scrollLeft > 0,
                more: node.scrollLeft + node.clientWidth < node.scrollWidth - 1,
              })
            }}
            className="h-full overflow-auto rounded-lg border bg-card"
            aria-busy={isLoading}
          >
            <table
              className="w-full border-separate border-spacing-0 text-left"
              style={{
                width: `max(100%, ${table.getTotalSize()}px)`,
                tableLayout: "fixed",
              }}
            >
              <thead>
                <tr>
                  {visibleHeaders.map((header, index) => {
                    const col = header.column.columnDef.meta as Column
                    const sorted =
                      state.sort?.replace(/^-/, "") === col.key
                        ? state.sort?.startsWith("-")
                          ? "desc"
                          : "asc"
                        : undefined
                    const filterActive = col.key in state.filters
                    const pinned = stickyFirst && index === 0
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
                        className={cn(
                          "sticky top-0 z-10 border-b bg-card px-3.5 py-2 text-xs font-medium text-muted-foreground",
                          pinned &&
                            "left-0 z-20 border-r border-border bg-card",
                        )}
                      >
                        <div
                          className={cn(
                            "flex items-center gap-1",
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
                              className="inline-flex min-w-0 items-center gap-1 text-left font-medium hover:text-foreground"
                            >
                              <span className="truncate">{col.label}</span>
                              {sorted === "asc" && (
                                <ArrowUp className="size-3.5 shrink-0 text-primary" />
                              )}
                              {sorted === "desc" && (
                                <ArrowDown className="size-3.5 shrink-0 text-primary" />
                              )}
                            </button>
                          ) : (
                            <span className="truncate">{col.label}</span>
                          )}
                          {filterPopovers && col.filterable && (
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
                        <td key={h.id} className="border-b px-3.5 py-3">
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
                          const pinned = stickyFirst && i === 0
                          return (
                            <td
                              key={cell.id}
                              style={{ width: cell.column.getSize() }}
                              className={cn(
                                "border-b px-3.5 py-2.5 align-middle whitespace-nowrap",
                                col.type === "number" && "text-right",
                                pinned &&
                                  "sticky left-0 z-[5] border-r border-border bg-card group-hover:bg-subtle",
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
          </div>
        )}
        {body && <div className="rounded-lg border bg-card">{body}</div>}
        {edges.more && (
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-y-0 right-0 w-10 rounded-r-lg bg-gradient-to-l from-card to-transparent"
          />
        )}
        {edges.scrolled && stickyFirst && (
          <div
            aria-hidden="true"
            className="pointer-events-none absolute inset-y-0 left-0 w-px bg-border"
          />
        )}
      </div>

      {!error && (rows.length > 0 || isLoading) && (
        <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 pt-3">
          <p>
            {!error && (total > 0 || !isLoading)
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
      )}
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
  options: Option[] | undefined
}) {
  const { t, locale } = useI18n()
  if (custom !== undefined) return <>{custom}</>
  if (value === null || value === undefined || value === "") return null

  switch (column.type) {
    case "number":
      return (
        <span className="block truncate">
          {formatNumber(Number(value), locale)}
        </span>
      )
    case "bool":
      return <>{value ? t("common.yes") : t("common.no")}</>
    case "datetime":
      return <Time value={String(value)} />
    case "enum": {
      const text = String(value)
      return (
        <span className="block truncate" title={text}>
          {options?.find((o) => o.value === text)?.label ?? text}
        </span>
      )
    }
    default:
      if (column.mono) {
        const text = String(value)
        return (
          <span className="flex min-w-0 items-center gap-1">
            <span className="truncate font-mono" title={text}>
              {text}
            </span>
            <CopyButton
              value={text}
              className="shrink-0 opacity-0 group-hover:opacity-100 focus-visible:opacity-100"
            />
          </span>
        )
      }
      return (
        <span className="block truncate" title={String(value)}>
          {String(value)}
        </span>
      )
  }
}
