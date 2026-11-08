import { createFileRoute } from "@tanstack/react-router"
import { Download } from "lucide-react"

import {
  type Column,
  DataTable,
  type FacetBucket,
  OptionList,
  type RowFlag,
  tableSearchSchema,
  useTableUrlState,
} from "@/components/data-table"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { type MessageKey, useI18n } from "@/i18n"
import { noData as list } from "@/lib/no-data"
import { resourceQuery } from "@/lib/resource-query"

export const Route = createFileRoute("/resources/")({
  validateSearch: tableSearchSchema,
  component: Resources,
})

// From GET /columns once the client has it; facets from GET /resources/facets.
const columns: Column[] = []
const facets: Record<string, FacetBucket[]> = {}

const SIDEBAR_GROUPS = [
  "accountId",
  "eimId",
  "baId",
  "accountType",
  "awsRegion",
  "resourceType",
] as const
const ALL_STATUSES = ["active", "missing", "deleted"]

type ResourceRow = {
  id: number
  status: "active" | "missing" | "deleted"
  stale: boolean
}

function flags(row: ResourceRow): RowFlag[] {
  const out: RowFlag[] = []
  if (row.status !== "active") out.push(row.status)
  if (row.stale) out.push("stale")
  return out
}

function Resources() {
  const { t } = useI18n()
  const { state, setState } = useTableUrlState(Route.useSearch())

  const groupLabel = (key: string) => t(`resources.group.${key}` as MessageKey)
  const filterLabels = Object.fromEntries(
    [...SIDEBAR_GROUPS, "status"].map((k) => [k, groupLabel(k)]),
  )
  const includeDeleted = Array.isArray(state.filters.status)
    ? state.filters.status.includes("deleted")
    : false

  function setFilter(key: string, value: string[] | undefined) {
    const filters = { ...state.filters }
    if (value?.length) filters[key] = value
    else delete filters[key]
    setState({ ...state, filters, page: 1 })
  }

  const exportHref = (format: "csv" | "jsonl") =>
    `/api/v1/resources/export?${resourceQuery(state, { format })}`

  return (
    <div className="flex h-full flex-col gap-4 px-6 py-6">
      <h1>{t("resources.title")}</h1>
      <div className="flex min-h-0 flex-1 gap-6">
        <aside
          aria-label={t("resources.filters")}
          className="flex w-60 shrink-0 flex-col gap-5 overflow-auto"
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
          {SIDEBAR_GROUPS.filter((k) => facets[k]?.length).map((key) => (
            <section key={key} className="flex flex-col gap-1.5">
              <h3>{groupLabel(key)}</h3>
              <OptionList
                options={facets[key]}
                selected={
                  Array.isArray(state.filters[key])
                    ? (state.filters[key] as string[])
                    : []
                }
                onChange={(next) => setFilter(key, next)}
              />
            </section>
          ))}
        </aside>
        <div className="min-w-0 flex-1">
          <DataTable<ResourceRow>
            tableId="resources"
            entity="resources"
            columns={columns}
            rows={[]}
            total={0}
            state={state}
            onStateChange={setState}
            facets={facets}
            filterLabels={filterLabels}
            isLoading={list.isLoading}
            error={list.error}
            onRetry={list.refetch}
            getRowFlags={flags}
            toolbarStart={
              <input
                type="search"
                defaultValue={state.q ?? ""}
                key={state.q ?? ""}
                placeholder={t("resources.searchPlaceholder")}
                aria-label={t("resources.searchPlaceholder")}
                onKeyDown={(e) => {
                  if (e.key === "Enter")
                    setState({
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
