import { createFileRoute, Link, useLocation } from "@tanstack/react-router"
import { useMemo, useState } from "react"

import {
  useAccount,
  useApplication,
  useColumns,
  useFacets,
  useResourceSummary,
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
  stateToSearch,
  type TableState,
  tableSearchSchema,
  useTableUrlState,
} from "@/components/data-table"
import { Breadcrumb } from "@/components/layout/crumbs"
import { Page, PageHeader, Panel } from "@/components/layout/page"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import {
  NAV_GROUPS,
  type NavGroup,
  type NavService,
} from "@/features/inventory-nav"
import { ResourceExport, ResourceFilters } from "@/features/resource-filters"
import { ResourcePreview } from "@/features/resource-preview"
import { ScanDialog } from "@/features/scan-dialog"
import { SERVICE_ICON } from "@/features/service-icons"
import { useI18n } from "@/i18n"
import { basePath } from "@/lib/base-path"
import { formatNumber } from "@/lib/format"
import {
  resourceParams,
  singleType,
  toSearchParams,
} from "@/lib/resource-query"
import { parseScope, scopeParams } from "@/lib/scope"
import { cn } from "@/lib/utils"

export const Route = createFileRoute("/resources/")({
  validateSearch: tableSearchSchema,
  component: Resources,
})

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

function Resources() {
  const { t, locale } = useI18n()
  const search = Route.useSearch()
  const location = useLocation()
  const { state, setState } = useTableUrlState(search)
  const scope = parseScope(search.s)
  const app = useApplication(scope?.kind === "app" ? scope.eim : undefined)
  const account = useAccount(scope?.kind === "acct" ? scope.id : undefined)
  const type = singleType(state)
  const types = useResourceTypes()
  const [preview, setPreview] = useState<ResourceItem | null>(null)
  const [scanOpen, setScanOpen] = useState(false)
  const columnsQuery = useColumns(type)
  const apiColumns = columnsQuery.data
  const ready = !!apiColumns && !columnsQuery.isPlaceholderData

  const columns = useMemo(
    () => buildColumns(apiColumns ?? [], type, scope?.kind ?? null, t),
    [apiColumns, type, scope, t],
  )

  // The menu's service and group links preset `resourceType`; those pages get
  // type tabs and a strip instead of the type filter.
  const selectedTypes = useMemo(
    () => asList(state.filters.resourceType),
    [state.filters.resourceType],
  )
  const service = useMemo(() => findService(selectedTypes), [selectedTypes])
  const group = useMemo(
    () => (service ? undefined : findGroup(selectedTypes)),
    [selectedTypes, service],
  )
  const tabTypes = service
    ? service.types
    : (group?.services.flatMap((s) => s.types) ?? [])

  const params = resourceParams(
    state,
    apiColumns ?? [],
    search.scanRequestId
      ? { ...scopeParams(scope), scanRequestId: [String(search.scanRequestId)] }
      : scopeParams(scope),
  )
  const list = useResources(
    params,
    { page: state.page, pageSize: state.pageSize, sort: state.sort },
    ready,
  )
  // Facets cap every field at 200 buckets; the stats need the real totals.
  const summary = useResourceSummary(params, ready)

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
    setState(stripTypeFilters(next))
  }

  function typeTabSearch(nextType: string) {
    const next = stripTypeFilters({
      ...state,
      filters: { ...state.filters, resourceType: [nextType] },
      page: 1,
    })
    return {
      ...stateToSearch(next),
      s: search.s,
      scanRequestId: search.scanRequestId,
    }
  }

  function toggleStripFilter(key: string, value: string) {
    const active = asList(state.filters[key])[0] === value
    const filters = { ...state.filters }
    if (active) delete filters[key]
    else filters[key] = [value]
    changeState({ ...state, filters, page: 1 })
  }

  const exportHref = (format: "csv" | "jsonl") =>
    `${basePath}/api/v1/resources/export?${toSearchParams({
      ...params,
      format,
      lang: locale,
      ...(state.sort ? { sort: state.sort } : {}),
    })}`

  const total = list.data?.total ?? 0
  const typeColumns = (apiColumns ?? []).filter((c) => c.scope === "type")
  const stripColumn = typeColumns.find((c) => c.type === "enum")
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

  const scanId = search.scanRequestId
  const typePage = !scanId && (service !== undefined || group !== undefined)
  const typeCounts = new Map(
    (facets.resourceType ?? []).map((option) => [option.value, option.count]),
  )
  const typeLabel = (name: string) =>
    facets.resourceType?.find((option) => option.value === name)?.label ??
    types.data?.find((item) => item.typeName === name)?.label ??
    name
  const title = scanId
    ? t("resources.scanTitle", { id: scanId })
    : service
      ? service.label
      : group
        ? t(`nav.group.${group.key}`)
        : t("resources.title")
  const scopeName =
    scope?.kind === "app"
      ? (app.data?.eimApp ?? scope.eim)
      : scope?.kind === "acct"
        ? (account.data?.accountName ?? scope.id)
        : undefined
  const ServiceIcon = service ? SERVICE_ICON[service.key] : undefined
  const parentGroup = service
    ? NAV_GROUPS.find((item) => item.services.includes(service))
    : undefined
  const description = scanId
    ? t("resources.scanNote")
    : service
      ? service.types.map(typeLabel).join(t("common.separator"))
      : group
        ? group.services.map((item) => item.label).join(t("common.separator"))
        : (scopeName ?? t("overview.allSubtitle"))

  return (
    <Page className="min-h-0 gap-4">
      <div className="flex flex-col gap-3">
        <Breadcrumb
          items={[
            {
              key: "inventory",
              content: (
                <Link to="/" search={{ s: search.s }}>
                  {t("nav.inventory")}
                </Link>
              ),
            },
            ...(scope
              ? [
                  {
                    key: "scope-parent",
                    content:
                      scope.kind === "app" ? (
                        <Link to="/apps">{t("apps.title")}</Link>
                      ) : (
                        <Link to="/accounts">{t("accounts.title")}</Link>
                      ),
                  },
                  {
                    key: "scope",
                    content: (
                      <Link
                        to="/"
                        search={{ s: search.s }}
                        className="text-muted-foreground"
                      >
                        {scopeName}
                      </Link>
                    ),
                  },
                ]
              : []),
            ...(service && parentGroup
              ? [
                  {
                    key: "service-group",
                    content: t(`nav.group.${parentGroup.key}`),
                  },
                ]
              : []),
            ...(scanId
              ? [
                  {
                    key: "scans",
                    content: <Link to="/scans">{t("scans.title")}</Link>,
                  },
                ]
              : []),
            {
              key: "page",
              content: service
                ? service.label
                : group
                  ? t(`nav.group.${group.key}`)
                  : scanId
                    ? t("resources.scanTitle", { id: scanId })
                    : t("resources.title"),
            },
          ]}
        />
        <PageHeader
          title={
            <>
              {ServiceIcon && (
                <span className="inline-grid size-9 shrink-0 place-items-center rounded-lg bg-primary-soft text-primary">
                  <ServiceIcon className="size-5" />
                </span>
              )}
              {title}
            </>
          }
          description={description}
          actions={
            scanId ? (
              <Link
                to="/scans/$id"
                params={{ id: String(scanId) }}
                className="text-sm text-primary hover:underline"
              >
                {t("resources.backToScan", { id: scanId })}
              </Link>
            ) : scope && !typePage ? (
              <>
                <Button variant="outline" asChild>
                  <Link to="/">{t("overview.allScope")}</Link>
                </Button>
                {scope.kind === "acct" && (
                  <Button variant="outline" asChild>
                    <Link
                      to="/accounts/$accountId"
                      params={{ accountId: scope.id }}
                    >
                      {t("overview.viewAccount")}
                    </Link>
                  </Button>
                )}
                <Button onClick={() => setScanOpen(true)}>
                  {t(
                    scope.kind === "app"
                      ? "overview.scanApp"
                      : "accounts.scanThis",
                  )}
                </Button>
              </>
            ) : undefined
          }
        />
      </div>

      {service && tabTypes.length > 1 && (
        <nav
          aria-label={t("resources.group.resourceType")}
          className="flex flex-wrap gap-1 overflow-x-auto border-b"
        >
          {tabTypes.map((typeName) => {
            const active =
              selectedTypes.length === 1 && selectedTypes[0] === typeName
            return (
              <Link
                key={typeName}
                to="/resources"
                search={typeTabSearch(typeName)}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "-mb-px flex items-center gap-1.5 border-b-2 px-3 py-1.5 text-sm whitespace-nowrap",
                  active
                    ? "border-primary font-semibold text-primary"
                    : "border-transparent text-muted-foreground hover:text-foreground",
                )}
              >
                {typeLabel(typeName)}
                {typeCounts.has(typeName) && (
                  <span className="text-xs tabular-nums">
                    {formatNumber(typeCounts.get(typeName) as number, locale)}
                  </span>
                )}
              </Link>
            )
          })}
        </nav>
      )}

      {group && !scanId && (
        <GroupOverview
          group={group}
          total={total}
          appCount={summary.data?.applications ?? 0}
          accountCount={summary.data?.accounts ?? 0}
          facets={facets}
          state={state}
          onChange={changeState}
        />
      )}

      {service && (
        <TypeStrip
          total={total}
          accountCount={summary.data?.accounts ?? 0}
          appCount={summary.data?.applications ?? 0}
          showAppCount={scope === null}
          statusLabel={stripColumn?.label}
          statusFilterKey={stripColumn?.key}
          statuses={stripColumn ? (facets[stripColumn.key] ?? []) : []}
          statusActive={
            stripColumn ? asList(state.filters[stripColumn.key])[0] : undefined
          }
          envs={facets.accountType ?? []}
          regions={facets.awsRegion ?? []}
          envActive={asList(state.filters.accountType)[0]}
          regionActive={asList(state.filters.awsRegion)[0]}
          showEnv={scope?.kind !== "acct"}
          onToggle={toggleStripFilter}
        />
      )}

      <div className="min-h-0 flex-1">
        <DataTable<ResourceItem>
          tableId={type ? "resources-type" : "resources"}
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
          preservedFilters={typePage ? ["resourceType"] : undefined}
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
              hideType={typePage}
            />
          }
        />
      </div>
      <ResourcePreview
        resource={preview}
        typeColumns={typeColumns}
        typeLabel={previewTypeLabel}
        back={back}
        onClose={() => setPreview(null)}
      />
      {scanOpen && (
        <ScanDialog
          open={scanOpen}
          onClose={() => setScanOpen(false)}
          preset={
            scope?.kind === "app"
              ? { eimId: scope.eim }
              : scope?.kind === "acct"
                ? { accountIds: [scope.id] }
                : undefined
          }
        />
      )}
    </Page>
  )
}

function GroupOverview({
  group,
  total,
  appCount,
  accountCount,
  facets,
  state,
  onChange,
}: {
  group: NavGroup
  total: number
  appCount: number
  accountCount: number
  facets: Record<string, Option[]>
  state: TableState
  onChange: (next: TableState) => void
}) {
  const { t, locale } = useI18n()
  const types = group.services.flatMap((service) => service.types)
  const byService = group.services
    .map((service) => ({
      key: service.key,
      label: service.label,
      count: service.types.reduce(
        (sum, type) =>
          sum +
          (facets.resourceType?.find((item) => item.value === type)?.count ??
            0),
        0,
      ),
    }))
    .sort((a, b) => b.count - a.count)
  const maxService = Math.max(0, ...byService.map((item) => item.count))

  function toggle(key: "accountType" | "awsRegion", value: string) {
    const values = asList(state.filters[key])
    const filters = { ...state.filters }
    if (values[0] === value) delete filters[key]
    else filters[key] = [value]
    onChange({ ...state, filters, page: 1 })
  }

  function active(key: "accountType" | "awsRegion", value: string) {
    return asList(state.filters[key])[0] === value
  }

  const cells = [
    {
      label: t("overview.statResources"),
      value: total,
      unit: t("overview.unitCount"),
    },
    {
      label: t("overview.statApps"),
      value: appCount,
      unit: t("overview.unitCount"),
    },
    {
      label: t("overview.statAccounts"),
      value: accountCount,
      unit: t("overview.unitCount"),
    },
    {
      label: t("overview.statTypes"),
      value: types.length,
      unit: t("overview.unitTypes"),
    },
    {
      label: t("overview.statRegions"),
      value: facets.awsRegion?.length ?? 0,
      unit: t("overview.unitCount"),
    },
  ]

  return (
    <>
      <dl className="grid grid-cols-2 overflow-hidden rounded-lg border bg-card md:grid-cols-3 xl:grid-cols-5">
        {cells.map((cell, index) => (
          <div
            key={cell.label}
            className="min-w-0 border-l border-border px-5 py-3.5 first:border-l-0"
          >
            <dt className="text-xs font-semibold text-muted-foreground">
              {cell.label}
            </dt>
            <dd className="mt-1 text-xs text-muted-foreground">
              <strong
                className={cn(
                  "mr-1 text-[28px] leading-tight font-semibold tracking-[-0.5px] text-foreground",
                  index === 0 && "text-primary",
                )}
              >
                {formatNumber(cell.value, locale)}
              </strong>
              {cell.unit}
            </dd>
          </div>
        ))}
      </dl>

      <div className="grid items-start gap-x-8 gap-y-6 md:grid-cols-2 xl:grid-cols-4">
        <Panel title={t("overview.byService")}>
          <BarList
            items={byService.map((item) => ({
              key: item.key,
              label: item.label,
              count: item.count,
              max: maxService,
            }))}
          />
        </Panel>
        <Panel title={t("overview.byApp")}>
          <BarList
            items={(facets.eimId ?? []).slice(0, 5).map((item) => ({
              key: item.value,
              label: item.label,
              count: item.count ?? 0,
              href: {
                s: `app:${item.value}`,
                f: { resourceType: types },
              },
            }))}
          />
        </Panel>
        <Panel title={t("overview.byEnv")}>
          <BarList
            items={(facets.accountType ?? []).map((item) => ({
              key: item.value,
              label: item.label,
              count: item.count ?? 0,
              active: active("accountType", item.value),
              onClick: () => toggle("accountType", item.value),
            }))}
          />
        </Panel>
        <Panel title={t("overview.byRegion")}>
          <BarList
            items={(facets.awsRegion ?? []).map((item) => ({
              key: item.value,
              label: item.label,
              count: item.count ?? 0,
              active: active("awsRegion", item.value),
              onClick: () => toggle("awsRegion", item.value),
            }))}
          />
        </Panel>
      </div>
    </>
  )
}

function BarList({
  items,
}: {
  items: {
    key: string
    label: string
    count: number
    max?: number
    active?: boolean
    href?: { s?: string; f?: Record<string, string[]> }
    onClick?: () => void
  }[]
}) {
  const { locale } = useI18n()
  if (items.length === 0)
    return <p className="px-2 py-2 text-sm text-muted-foreground">—</p>
  const max = Math.max(1, ...items.map((item) => item.max ?? item.count))
  return (
    <ul className="flex flex-col">
      {items.map((item) => {
        const inner = (
          <>
            <span className="truncate text-sm">{item.label}</span>
            <span className="text-sm tabular-nums">
              {formatNumber(item.count, locale)}
            </span>
            <span className="col-span-2 mt-1 h-1 rounded-full bg-subtle-strong">
              <span
                className="block h-full rounded-full bg-primary"
                style={{ width: `${(item.count / max) * 100}%` }}
              />
            </span>
          </>
        )
        const className = cn(
          "grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 rounded-md px-2 py-1.5 text-left",
          item.active
            ? "bg-primary-soft font-semibold text-primary"
            : "hover:bg-subtle",
        )
        return (
          <li key={item.key}>
            {item.onClick ? (
              <button
                type="button"
                aria-pressed={item.active}
                onClick={item.onClick}
                className={className}
              >
                {inner}
              </button>
            ) : item.href ? (
              <Link to="/resources" search={item.href} className={className}>
                {inner}
              </Link>
            ) : (
              <div className={className}>{inner}</div>
            )}
          </li>
        )
      })}
    </ul>
  )
}

function buildColumns(
  apiColumns: ApiColumn[],
  type: string | undefined,
  scoped: "app" | "acct" | null,
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
  // Only an account scope fixes the account; an application spans several.
  if (account && scoped !== "acct")
    out.push({
      ...account,
      key: "accountId",
      label: t("resources.group.accountId"),
    })

  const app = byKey.get("eimId")
  if (app && !scoped)
    out.push({ ...app, key: "eimId", label: t("resources.column.application") })

  const env = byKey.get("accountType")
  const region = byKey.get("awsRegion")
  if (env && scoped !== "acct")
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

/** The service or group header's strip: how many resources, and where they are. */
function TypeStrip({
  total,
  accountCount,
  appCount,
  showAppCount,
  statusLabel,
  statuses,
  statusActive,
  statusFilterKey,
  envs,
  regions,
  envActive,
  regionActive,
  showEnv,
  onToggle,
}: {
  total: number
  accountCount: number
  appCount: number
  showAppCount: boolean
  statusLabel: string | undefined
  statuses: Option[]
  statusActive: string | undefined
  statusFilterKey: string | undefined
  envs: Option[]
  regions: Option[]
  envActive: string | undefined
  regionActive: string | undefined
  showEnv: boolean
  onToggle: (key: string, value: string) => void
}) {
  const { t, locale } = useI18n()
  return (
    <dl
      className={cn(
        "grid grid-cols-1 divide-y overflow-hidden rounded-lg border bg-card sm:divide-x sm:divide-y-0",
        statusLabel
          ? "sm:grid-cols-[minmax(230px,1.1fr)_minmax(0,0.9fr)_minmax(0,1fr)_minmax(0,1fr)]"
          : "sm:grid-cols-[minmax(230px,1.2fr)_minmax(0,1fr)_minmax(0,1fr)]",
      )}
    >
      <div className="px-5 py-3">
        <dt className="text-xs font-semibold text-muted-foreground">
          {t("overview.statResources")}
        </dt>
        <dd className="mt-1 text-xs text-muted-foreground">
          <strong className="mr-1 text-[28px] leading-tight font-semibold tracking-[-0.5px] text-primary">
            {formatNumber(total, locale)}
          </strong>
          {t("overview.unitCount")}
          {showAppCount && appCount > 0 && (
            <span>
              {t("common.separator")}
              {t("overview.appCount", { count: appCount })}
            </span>
          )}
          {accountCount > 0 && (
            <span>
              {t("common.separator")}
              {t("overview.accountCount", { count: accountCount })}
            </span>
          )}
        </dd>
      </div>
      {statusLabel && statuses.length > 0 && (
        <PillsCell
          label={statusLabel}
          options={statuses}
          active={statusActive}
          onToggle={(value) => onToggle(statusFilterKey as string, value)}
        />
      )}
      {showEnv && (
        <PillsCell
          label={t("resources.group.accountType")}
          options={envs}
          active={envActive}
          onToggle={(value) => onToggle("accountType", value)}
        />
      )}
      <PillsCell
        label={t("resources.group.awsRegion")}
        options={regions}
        active={regionActive}
        onToggle={(value) => onToggle("awsRegion", value)}
      />
    </dl>
  )
}

function PillsCell({
  label,
  options,
  active,
  onToggle,
}: {
  label: string
  options: Option[]
  active: string | undefined
  onToggle: (value: string) => void
}) {
  const { t, locale } = useI18n()
  return (
    <div className="px-5 py-3">
      <dt className="text-xs font-semibold text-muted-foreground">{label}</dt>
      <dd className="mt-1 flex flex-wrap items-center gap-1.5">
        {options.length === 0 && (
          <span className="text-xs text-muted-foreground">
            {t("common.none")}
          </span>
        )}
        {options.map((option) => {
          const on = option.value === active
          return (
            <button
              key={option.value}
              type="button"
              aria-pressed={on}
              onClick={() => onToggle(option.value)}
              className={cn(
                "inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs",
                on
                  ? "border-primary bg-primary-soft font-semibold text-primary"
                  : "border-border hover:bg-subtle",
              )}
            >
              {option.label}
              {option.count !== undefined && (
                <span
                  className={cn("tabular-nums", !on && "text-muted-foreground")}
                >
                  {formatNumber(option.count, locale)}
                </span>
              )}
            </button>
          )
        })}
      </dd>
    </div>
  )
}

function asList(value: unknown): string[] {
  if (Array.isArray(value)) return value.map(String)
  return typeof value === "string" && value ? [value] : []
}

/** The service every selected type belongs to, if there is one. */
function findService(types: string[]): NavService | undefined {
  if (types.length === 0) return undefined
  return NAV_GROUPS.flatMap((group) => group.services).find((service) =>
    types.every((type) => service.types.includes(type)),
  )
}

/** The menu group every selected type belongs to, if there is one. */
function findGroup(types: string[]): NavGroup | undefined {
  if (types.length === 0) return undefined
  return NAV_GROUPS.find((group) => {
    const known = new Set(group.services.flatMap((service) => service.types))
    return types.every((type) => known.has(type))
  })
}
