import { Link, useLocation } from "@tanstack/react-router"
import { useMemo, useState } from "react"

import {
  useColumns,
  useFacets,
  useResources,
  useResourceTypes,
} from "@/api/hooks"
import type { Column as ApiColumn, ResourceItem } from "@/client"
import { CopyButton } from "@/components/copy-button"
import {
  type Column,
  DataTable,
  type Option,
  type RowFlag,
  type TableState,
} from "@/components/data-table"
import { Badge } from "@/components/ui/badge"
import { ResourceExport, ResourceFilters } from "@/features/resource-filters"
import { ResourcePreview } from "@/features/resource-preview"
import { useI18n } from "@/i18n"
import { basePath } from "@/lib/base-path"
import { formatNumber } from "@/lib/format"
import {
  resourceParams,
  singleType,
  toSearchParams,
} from "@/lib/resource-query"
import { type Scope, scopeParams } from "@/lib/scope"
import { cn } from "@/lib/utils"

/** Columns kept in the columns menu but hidden until the user asks for them. */
const EXTRA_COLUMNS = [
  "arn",
  "baId",
  "tags",
  "status",
  "configurationItemCaptureTime",
  "firstSeenAt",
  "lastChangedAt",
  "missingSince",
  "deletedAt",
]

const COLUMN_WIDTHS: Record<string, number> = {
  resourceName: 260,
  accountId: 180,
  eimId: 160,
  accountType: 108,
  awsRegion: 120,
  resourceType: 180,
}

const FLAG_TONE: Record<RowFlag, "warn" | "neutral"> = {
  missing: "warn",
  deleted: "neutral",
  stale: "neutral",
}

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

/**
 * The overview's resource list (dashboard.md 3.1): the same filter row and table
 * as the resources page, in the current application or account scope.
 */
export function OverviewResources({
  state,
  onStateChange,
  scope,
  fallbackTotal,
}: {
  state: TableState
  onStateChange: (next: TableState) => void
  scope: Scope | null
  /** Scope-wide count, shown until the first page of the filtered list arrives. */
  fallbackTotal: number
}) {
  const { t, locale } = useI18n()
  const location = useLocation()
  const type = singleType(state)
  const scopeKind = scope?.kind ?? null
  const types = useResourceTypes()
  const [preview, setPreview] = useState<ResourceItem | null>(null)
  const columnsQuery = useColumns(type)
  const apiColumns = columnsQuery.data
  const ready = !!apiColumns && !columnsQuery.isPlaceholderData

  const columns = useMemo(
    () => buildColumns(apiColumns ?? [], type, scopeKind, t),
    [apiColumns, type, scopeKind, t],
  )

  const params = resourceParams(state, apiColumns ?? [], scopeParams(scope))
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
    [
      "accountId",
      "eimId",
      "baId",
      "accountType",
      "awsRegion",
      "resourceType",
      ...attrFacets,
    ],
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

  // Type columns exist only while exactly one type is chosen; their filters go with it.
  function stripTypeFilters(next: TableState): TableState {
    if (singleType(next) === type) return next
    const typeKeys = new Set(
      (apiColumns ?? []).filter((c) => c.scope === "type").map((c) => c.key),
    )
    return {
      ...next,
      filters: Object.fromEntries(
        Object.entries(next.filters).filter(([k]) => !typeKeys.has(k)),
      ),
    }
  }
  function changeState(next: TableState) {
    onStateChange(stripTypeFilters(next))
  }

  const exportHref = (format: "csv" | "jsonl") =>
    `${basePath}/api/v1/resources/export?${toSearchParams({
      ...params,
      format,
      lang: locale,
      ...(state.sort ? { sort: state.sort } : {}),
    })}`

  const total = list.data?.total ?? fallbackTotal
  const typeColumns = (apiColumns ?? []).filter((c) => c.scope === "type")
  const previewTypeLabel = preview
    ? (types.data?.find((x) => x.typeName === preview.resourceType)?.label ??
      preview.resourceType)
    : ""
  const back = `${location.pathname}${location.searchStr}${
    location.hash ? `#${location.hash}` : ""
  }`
  const columnWidths = useMemo(
    () => ({
      ...COLUMN_WIDTHS,
      ...Object.fromEntries(
        (apiColumns ?? [])
          .filter((c) => c.scope === "type")
          .map((c) => [c.key, 150]),
      ),
    }),
    [apiColumns],
  )

  const hint = t("overview.resourceHint")

  return (
    <section className="flex min-w-0 flex-col gap-3">
      <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
        <h2>
          {t("resources.title")}{" "}
          <span className="text-xs font-normal text-muted-foreground">
            {formatNumber(total, locale)} {t("overview.unitCount")}
          </span>
        </h2>
        <span className="text-sm text-muted-foreground">{hint}</span>
      </div>
      <DataTable<ResourceItem>
        tableId={type ? "overview-type" : "overview"}
        entity="resources"
        columns={columns}
        rows={list.data?.items ?? []}
        total={total}
        state={state}
        onStateChange={changeState}
        facets={facets}
        isLoading={columnsQuery.isLoading || list.isLoading || !ready}
        error={columnsQuery.error ?? list.error}
        onRetry={() => {
          columnsQuery.refetch()
          list.refetch()
          facetQuery.refetch()
        }}
        getRowId={(row) => String(row.id)}
        getCellValue={cellValue}
        renderCell={(column, row) => renderCell(column, row, facets, t, back)}
        defaultHidden={EXTRA_COLUMNS}
        columnWidths={columnWidths}
        stickyFirst
        filterPopovers={false}
        onRowActivate={setPreview}
        rowLabel={(row) =>
          t("preview.row", { name: row.resourceName ?? row.resourceId })
        }
        toolbarEnd={<ResourceExport total={total} exportHref={exportHref} />}
        toolbar={
          <ResourceFilters
            state={state}
            onStateChange={changeState}
            facets={facets}
            columns={apiColumns ?? []}
            scope={scope}
          />
        }
      />
      <ResourcePreview
        resource={preview}
        typeColumns={typeColumns}
        typeLabel={previewTypeLabel}
        back={back}
        onClose={() => setPreview(null)}
      />
    </section>
  )
}

function buildColumns(
  apiColumns: ApiColumn[],
  type: string | undefined,
  scopeKind: "app" | "acct" | null,
  t: ReturnType<typeof useI18n>["t"],
): Column[] {
  const byKey = new Map(apiColumns.map((c) => [c.key, c]))
  const out: Column[] = []

  const identity = byKey.get("resourceName")
  if (identity)
    out.push({
      ...identity,
      key: "resourceName",
      label: t("resources.column.identity"),
    })

  const account = byKey.get("accountId")
  if (account && scopeKind !== "acct")
    out.push({
      ...account,
      key: "accountId",
      label: t("resources.group.accountId"),
    })

  const app = byKey.get("eimId")
  if (app && scopeKind === null)
    out.push({ ...app, key: "eimId", label: t("resources.column.application") })

  const env = byKey.get("accountType")
  const region = byKey.get("awsRegion")
  if (env && scopeKind !== "acct")
    out.push({ ...env, label: t("resources.group.accountType") })
  if (region) out.push({ ...region, label: t("resources.group.awsRegion") })

  const resourceType = byKey.get("resourceType")
  if (resourceType && !type)
    out.push({ ...resourceType, label: t("overview.statTypes") })

  if (type) out.push(...apiColumns.filter((c) => c.scope === "type"))

  for (const key of EXTRA_COLUMNS) {
    const column = byKey.get(key)
    if (column && !out.some((c) => c.key === key)) out.push(column)
  }
  return out
}

function renderCell(
  column: Column,
  row: ResourceItem,
  facets: Record<string, Option[]>,
  t: ReturnType<typeof useI18n>["t"],
  back: string,
) {
  const { account } = row
  switch (column.key) {
    case "resourceName":
      return <IdentityCell row={row} back={back} />
    case "accountId":
      return (
        <div className="min-w-0">
          <Link
            to="/"
            search={{ s: `acct:${account.accountId}` }}
            title={account.accountName}
            className="block truncate font-medium text-primary hover:underline"
          >
            {account.accountName}
          </Link>
          <span className="flex min-w-0 items-center gap-0.5 font-mono text-xs text-muted-foreground">
            <span className="truncate">{account.accountId}</span>
            <CopyButton value={account.accountId} className="size-5 shrink-0" />
          </span>
        </div>
      )
    case "eimId":
      return (
        <div className="min-w-0">
          <Link
            to="/"
            search={{ s: `app:${account.eimId}` }}
            title={account.eimApp ?? account.eimId ?? ""}
            className="block truncate text-primary hover:underline"
          >
            {account.eimApp ?? account.eimId ?? ""}
          </Link>
          <span className="flex min-w-0 items-center gap-0.5 font-mono text-xs text-muted-foreground">
            <span className="truncate">{account.eimId}</span>
            {account.eimId && (
              <CopyButton value={account.eimId} className="size-5 shrink-0" />
            )}
          </span>
        </div>
      )
    case "accountType":
      return t(`env.${account.accountType}`)
    case "awsRegion":
      return row.awsRegion === "global" ? t("region.global") : undefined
    case "resourceType": {
      const label =
        facets.resourceType?.find((o) => o.value === row.resourceType)?.label ??
        row.resourceType
      return (
        <span className="block truncate" title={row.resourceType}>
          {label}
        </span>
      )
    }
    case "tags":
      return row.tags.length ? (
        <ul className="flex flex-col">
          {row.tags.map((tag) => (
            <li key={tag.key} className="truncate">
              {tag.key}={tag.value}
            </li>
          ))}
        </ul>
      ) : (
        <span className="text-muted-foreground">{t("common.none")}</span>
      )
    default:
      return undefined
  }
}

function IdentityCell({ row, back }: { row: ResourceItem; back: string }) {
  const { t } = useI18n()
  return (
    <div className="min-w-0">
      <div className="flex min-w-0 items-center gap-1.5">
        <Link
          to="/resources/$id"
          params={{ id: String(row.id) }}
          search={{ back }}
          title={row.resourceName ?? row.resourceId}
          className="truncate font-semibold text-primary hover:underline"
        >
          {row.resourceName ?? row.resourceId}
        </Link>
        {flags(row).map((flag) => (
          <Badge key={flag} tone={FLAG_TONE[flag]}>
            {t(`status.${flag}`)}
          </Badge>
        ))}
      </div>
      <span className="flex min-w-0 items-center gap-0.5">
        <span
          title={row.resourceId}
          className={cn(
            "truncate font-mono text-xs text-muted-foreground",
            !row.resourceName && "text-sm text-foreground",
          )}
        >
          {row.resourceId}
        </span>
        <CopyButton value={row.resourceId} className="size-5 shrink-0" />
      </span>
    </div>
  )
}
