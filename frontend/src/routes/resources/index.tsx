import { createFileRoute, Link } from "@tanstack/react-router"
import { Download } from "lucide-react"
import { useMemo } from "react"

import { useColumns, useFacets, useResources } from "@/api/hooks"
import type { ResourceItem } from "@/client"
import {
  type Column,
  DataTable,
  type Option,
  OptionList,
  type RowFlag,
  type TableState,
  tableSearchSchema,
  useTableUrlState,
} from "@/components/data-table"
import { InlineError } from "@/components/inline-state"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { TagFilter } from "@/features/tag-filter"
import { type MessageKey, useI18n } from "@/i18n"
import { errorText } from "@/lib/errors"
import {
  resourceParams,
  singleType,
  toSearchParams,
} from "@/lib/resource-query"

export const Route = createFileRoute("/resources/")({
  validateSearch: tableSearchSchema,
  component: Resources,
})

const SIDEBAR_GROUPS = [
  "accountId",
  "eimId",
  "baId",
  "accountType",
  "awsRegion",
  "resourceType",
] as const
const ALL_STATUSES = ["active", "missing", "deleted"]
// Shown after the API's columns; the shortest way to see how connected a resource is.
const RELATIONSHIP_COLUMN = "relationshipCount"
// The API's common columns that are rarely needed start hidden.
const DEFAULT_HIDDEN = [
  "arn",
  "accountId",
  "eimApp",
  "baId",
  "status",
  "configurationItemCaptureTime",
  "firstSeenAt",
  "missingSince",
  "deletedAt",
]

// With one type chosen the type's own columns matter more than what the filter already says.
const TYPE_HIDDEN = [
  ...DEFAULT_HIDDEN,
  "resourceType",
  "eimId",
  "accountType",
  "tags",
]

function flags(row: ResourceItem): RowFlag[] {
  const out: RowFlag[] = []
  if (row.status !== "active") out.push(row.status)
  if (row.stale) out.push("stale")
  return out
}

function cellValue(row: ResourceItem, key: string): unknown {
  const r = row as unknown as Record<string, unknown>
  if (key in r) return r[key]
  const account = row.account as unknown as Record<string, unknown>
  if (key in account) return account[key]
  return row.attrs[key]
}

function asList(value: unknown): string[] {
  return Array.isArray(value) ? (value as string[]) : []
}

function Resources() {
  const { t, locale } = useI18n()
  const { state, setState } = useTableUrlState(Route.useSearch())
  const type = singleType(state)

  const columnsQuery = useColumns(type)
  const apiColumns = columnsQuery.data
  const ready = !!apiColumns && !columnsQuery.isPlaceholderData

  const columns = useMemo<Column[]>(() => {
    if (!apiColumns) return []
    return [
      ...apiColumns.filter((c) => c.scope === "common"),
      ...apiColumns.filter((c) => c.scope === "type"),
      {
        key: RELATIONSHIP_COLUMN,
        label: t("resources.relationships"),
        type: "number",
        mono: false,
        sortable: false,
        filterable: false,
        scope: "common",
      },
    ]
  }, [apiColumns, t])

  const params = resourceParams(state, apiColumns ?? [])
  const list = useResources(
    params,
    { page: state.page, pageSize: state.pageSize, sort: state.sort },
    ready,
  )

  const attrFacets = (apiColumns ?? [])
    .filter(
      (c) => c.scope === "type" && (c.type === "enum" || c.type === "bool"),
    )
    .map((c) => `attr.${c.key}`)
  const facetQuery = useFacets(
    [...SIDEBAR_GROUPS, "status", ...attrFacets],
    params,
    ready,
  )
  const facets = useMemo(() => {
    const out: Record<string, Option[]> = {}
    for (const [field, buckets] of Object.entries(facetQuery.data ?? {})) {
      out[field.replace(/^attr\./, "")] = buckets
    }
    return out
  }, [facetQuery.data])

  const groupLabel = (key: string) => t(`resources.group.${key}` as MessageKey)
  const filterLabels = Object.fromEntries(
    [...SIDEBAR_GROUPS, "status", "tagMissing"].map((k) => [k, groupLabel(k)]),
  )
  filterLabels.tag = groupLabel("tags")
  const includeDeleted = asList(state.filters.status).includes("deleted")

  // Type columns exist only while exactly one type is chosen; their filters go with it.
  function changeState(next: TableState) {
    if (singleType(next) !== type) {
      const typeKeys = new Set(
        (apiColumns ?? []).filter((c) => c.scope === "type").map((c) => c.key),
      )
      const filters = Object.fromEntries(
        Object.entries(next.filters).filter(([k]) => !typeKeys.has(k)),
      )
      next = { ...next, filters }
    }
    setState(next)
  }

  function setFilter(key: string, value: string[] | undefined) {
    const filters = { ...state.filters }
    if (value?.length) filters[key] = value
    else delete filters[key]
    changeState({ ...state, filters, page: 1 })
  }

  const exportHref = (format: "csv" | "jsonl") =>
    `/api/v1/resources/export?${toSearchParams({
      ...params,
      format,
      lang: locale,
      ...(state.sort ? { sort: state.sort } : {}),
    })}`

  return (
    <div className="flex h-full flex-col gap-4 px-6 py-6">
      <h1>{t("resources.title")}</h1>
      <div className="flex min-h-0 flex-1 gap-6">
        <aside
          aria-label={t("resources.filters")}
          className="flex w-64 shrink-0 flex-col gap-5 overflow-auto pr-1"
        >
          <label className="flex items-center gap-2">
            <input
              type="checkbox"
              checked={includeDeleted}
              onChange={(e) =>
                setFilter("status", e.target.checked ? ALL_STATUSES : undefined)
              }
              className="accent-[var(--accent)]"
            />
            {t("resources.includeDeleted")}
          </label>
          {facetQuery.error && (
            <InlineError
              message={`${t("resources.facetsError")}（${errorText(t, facetQuery.error)}）`}
              onRetry={() => facetQuery.refetch()}
            />
          )}
          {SIDEBAR_GROUPS.filter((k) => facets[k]?.length).map((key) => (
            <section key={key} className="flex flex-col gap-1.5">
              <h3>{groupLabel(key)}</h3>
              <OptionList
                options={facets[key]}
                selected={asList(state.filters[key])}
                onChange={(next) => setFilter(key, next)}
              />
            </section>
          ))}
          <section className="flex flex-col gap-1.5">
            <h3>{groupLabel("tags")}</h3>
            <TagFilter
              value={{
                tag: asList(state.filters.tag),
                tagMissing: asList(state.filters.tagMissing),
              }}
              onChange={(next) => {
                const filters = { ...state.filters }
                for (const k of ["tag", "tagMissing"] as const) {
                  if (next[k].length) filters[k] = next[k]
                  else delete filters[k]
                }
                changeState({ ...state, filters, page: 1 })
              }}
            />
          </section>
        </aside>
        <div className="min-w-0 flex-1">
          <DataTable<ResourceItem>
            tableId={type ? "resources-type" : "resources"}
            entity="resources"
            columns={columns}
            rows={list.data?.items ?? []}
            total={list.data?.total ?? 0}
            state={state}
            onStateChange={changeState}
            facets={facets}
            filterLabels={filterLabels}
            isLoading={columnsQuery.isLoading || list.isLoading || !ready}
            error={columnsQuery.error ?? list.error}
            onRetry={() => {
              columnsQuery.refetch()
              list.refetch()
            }}
            getRowId={(row) => String(row.id)}
            getCellValue={cellValue}
            renderCell={renderCell}
            getRowFlags={flags}
            defaultHidden={type ? TYPE_HIDDEN : DEFAULT_HIDDEN}
            toolbarStart={
              <input
                type="search"
                defaultValue={state.q ?? ""}
                key={state.q ?? ""}
                placeholder={t("resources.searchPlaceholder")}
                aria-label={t("resources.searchPlaceholder")}
                onKeyDown={(e) => {
                  if (e.key === "Enter")
                    changeState({
                      ...state,
                      q: e.currentTarget.value.trim() || undefined,
                      page: 1,
                    })
                }}
                className="h-8 w-64 rounded-md border border-input bg-card px-2.5 placeholder:text-muted-foreground"
              />
            }
            toolbarEnd={
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <Button variant="outline">
                    <Download />
                    {t("action.export")}
                  </Button>
                </DropdownMenuTrigger>
                <DropdownMenuContent>
                  <DropdownMenuItem asChild>
                    <a href={exportHref("csv")}>{t("action.exportCsv")}</a>
                  </DropdownMenuItem>
                  <DropdownMenuItem asChild>
                    <a href={exportHref("jsonl")}>{t("action.exportJsonl")}</a>
                  </DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            }
          />
        </div>
      </div>
    </div>
  )
}

function renderCell(column: Column, row: ResourceItem) {
  switch (column.key) {
    case "resourceName":
      return (
        <Link
          to="/resources/$id"
          params={{ id: String(row.id) }}
          className="text-primary hover:underline"
        >
          {row.resourceName ?? row.resourceId}
        </Link>
      )
    case "tags":
      return (
        <ul className="flex flex-col">
          {row.tags.map((tag) => (
            <li key={tag.key} className="break-all">
              {tag.key}={tag.value}
            </li>
          ))}
        </ul>
      )
    case "status":
      return row.status === "active" ? null : undefined
    case RELATIONSHIP_COLUMN:
      return undefined
  }
  const value = cellValue(row, column.key)
  if (Array.isArray(value)) return <>{value.join(", ")}</>
  return undefined
}
