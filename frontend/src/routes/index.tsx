import { createFileRoute, Link } from "@tanstack/react-router"
import { type ComponentType, type ReactNode, useState } from "react"

import {
  useAccount,
  useAccounts,
  useApplication,
  useApplications,
  useColumns,
  useFacets,
  useOverview,
  useResourceSummary,
} from "@/api/hooks"
import type { AccountSummary } from "@/client"
import {
  stateToSearch,
  type TableSearch,
  tableSearchSchema,
  useTableUrlState,
} from "@/components/data-table"
import { Breadcrumb } from "@/components/layout/crumbs"
import { Page, PageHeader, Panel } from "@/components/layout/page"
import { Button } from "@/components/ui/button"
import { NAV_GROUPS } from "@/features/inventory-nav"
import { OverviewResources } from "@/features/overview-resources"
import { ScanDialog } from "@/features/scan-dialog"
import { SERVICE_ICON } from "@/features/service-icons"
import { type MessageKey, useI18n } from "@/i18n"
import { formatNumber } from "@/lib/format"
import { resourceParams, singleType } from "@/lib/resource-query"
import { parseScope, scopeParams, scopeValue, withScope } from "@/lib/scope"
import { cn } from "@/lib/utils"

export const Route = createFileRoute("/")({
  validateSearch: tableSearchSchema,
  component: Overview,
})

/** Resource type → the service (and its menu label) that lists it. */
const SERVICE_OF_TYPE = new Map(
  NAV_GROUPS.flatMap((group) =>
    group.services.flatMap((service) =>
      service.types.map((type) => [type, service] as const),
    ),
  ),
)

function Overview() {
  const { t, locale } = useI18n()
  const search = Route.useSearch()
  const { state, setState } = useTableUrlState(search)
  const scope = parseScope(search.s)
  const appEim = scope?.kind === "app" ? scope.eim : undefined
  const accountId = scope?.kind === "acct" ? scope.id : undefined

  const [scanOpen, setScanOpen] = useState(false)
  const overview = useOverview()
  const columns = useColumns(singleType(state))
  const params = resourceParams(state, columns.data ?? [], scopeParams(scope))
  const ready = !!columns.data && !columns.isPlaceholderData
  const summary = useResourceSummary(params, ready)
  const facets = useFacets(
    ["resourceType", "accountType", "awsRegion", "accountId"],
    params,
    ready,
  )
  const app = useApplication(appEim)
  const account = useAccount(accountId)
  const appAccounts = useAccounts(
    appEim ? { eimId: [appEim] } : {},
    { page: 1, pageSize: 200, sort: "-resourceCount" },
    !!appEim,
  )
  const appUnreachable = useAccounts(
    appEim
      ? {
          eimId: [appEim],
          accessStatus: ["access_denied", "credential_error"],
        }
      : {},
    { page: 1, pageSize: 1, sort: undefined },
    !!appEim,
  )
  const appsTotal = useApplications({}, { page: 1, pageSize: 1 })

  function matchingBuckets(key: string) {
    const values = state.filters[key]
    const selected = Array.isArray(values) ? values : values ? [values] : []
    return (facets.data?.[key] ?? []).filter(
      (item) => !selected.length || selected.includes(item.value),
    )
  }
  // A facet leaves out its own filter, so each distribution keeps every bucket
  // (the selected one is highlighted); only the stats follow the selection.
  const byType = facets.data?.resourceType ?? []
  const byEnv = facets.data?.accountType ?? []
  const byRegion = facets.data?.awsRegion ?? []
  const resourceTotal = summary.data?.resources ?? 0
  const typeTotal = summary.data?.resourceTypes ?? 0
  const regionTotal = summary.data?.regions ?? 0
  const services = serviceBars(byType)
  const filtered = !!state.q || Object.keys(state.filters).length > 0
  // The summary counts in SQL; facet buckets stop at 200 and would under-report a
  // large organization's applications and accounts.
  const appsCount = filtered
    ? (summary.data?.applications ?? 0)
    : (appsTotal.data?.total ?? 0)
  const accountsCount =
    filtered || scope !== null
      ? (summary.data?.accounts ?? 0)
      : (overview.data?.accountTotal ?? 0)
  const unreachable =
    scope === null
      ? (overview.data?.accountUnreachable ?? 0)
      : scope.kind === "app"
        ? (appUnreachable.data?.total ?? 0)
        : 0

  const title =
    scope === null
      ? t("nav.overview")
      : scope.kind === "app"
        ? (app.data?.eimApp ?? scope.eim)
        : (account.data?.accountName ?? scope.id)

  function selected(key: string, value: string): boolean {
    const current = state.filters[key]
    return Array.isArray(current) ? current.includes(value) : current === value
  }

  function toggleFilter(key: "accountType" | "awsRegion", value: string) {
    const filters = { ...state.filters }
    if (selected(key, value)) delete filters[key]
    else filters[key] = [value]
    setState({ ...state, filters, page: 1 })
  }

  /** The current table state with the scope switched, dropping filters scopes hide. */
  function scopeSearch(kind: "app" | "acct", id: string): TableSearch {
    const filters = { ...state.filters }
    delete filters.accountId
    delete filters.eimId
    if (kind === "acct") delete filters.accountType
    const next: TableSearch = {
      ...stateToSearch(state),
      page: undefined,
      s: kind === "app" ? `app:${id}` : `acct:${id}`,
    }
    if (Object.keys(filters).length) next.f = filters
    else delete next.f
    return next
  }

  function serviceSearch(types: string[]): TableSearch {
    const value = scopeValue(scope)
    return {
      ...stateToSearch(state),
      page: undefined,
      ...(value ? { s: value } : {}),
      f: { ...state.filters, resourceType: types },
    }
  }

  return (
    <Page className="gap-6">
      <div className="flex flex-col gap-3">
        <Breadcrumb
          items={[
            {
              key: "inventory",
              content: (
                <Link to="/" search={{ s: undefined }}>
                  {t("nav.inventory")}
                </Link>
              ),
            },
            scope === null
              ? { key: "overview", content: t("nav.overview") }
              : scope.kind === "app"
                ? {
                    key: "apps",
                    content: <Link to="/apps">{t("apps.title")}</Link>,
                  }
                : {
                    key: "accounts",
                    content: <Link to="/accounts">{t("accounts.title")}</Link>,
                  },
            ...(scope === null ? [] : [{ key: "scope", content: title }]),
          ]}
        />
        <PageHeader
          title={title}
          description={
            scope === null
              ? t("overview.allSubtitle")
              : scope.kind === "app"
                ? t("overview.appSubtitle", {
                    eim: scope.eim,
                    ba: app.data?.baId ?? t("common.none"),
                    accounts:
                      appAccounts.data?.total ?? app.data?.accountCount ?? 0,
                    envs:
                      app.data?.environments
                        .map((env) => t(`env.${env}`))
                        .join(t("common.separator")) ?? "",
                  })
                : t("overview.accountSubtitle", {
                    id: scope.id,
                    env: account.data
                      ? t(`env.${account.data.accountType}`)
                      : "",
                    app:
                      account.data?.eimApp ??
                      account.data?.eimId ??
                      t("common.none"),
                  })
          }
          actions={
            scope && (
              <>
                <Button variant="outline" asChild>
                  <Link to="/" search={{ s: undefined }}>
                    {t("overview.allScope")}
                  </Link>
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
            )
          }
        />
      </div>

      {scope?.kind === "acct" &&
        account.data &&
        account.data.accessStatus !== "ok" && (
          <div className="rounded-lg border border-warn/40 bg-warn-soft px-4 py-3">
            <strong>{t("overview.accountNotice")}</strong>
            <p className="mt-1 text-muted-foreground">
              {t("overview.accountNoticeBody", {
                reason: t(`access.${account.data.accessStatus}` as MessageKey),
              })}{" "}
              <Link
                to="/accounts/$accountId"
                params={{ accountId: scope.id }}
                className="text-primary hover:underline"
              >
                {t("overview.viewAccount")}
              </Link>
            </p>
          </div>
        )}

      <dl
        className={cn(
          "grid grid-cols-2 overflow-hidden rounded-lg border bg-card md:grid-cols-3",
          scope === null ? "xl:grid-cols-5" : "xl:grid-cols-4",
          scope?.kind === "acct" && "xl:grid-cols-3",
        )}
        aria-label={t("nav.overview")}
      >
        <Stat
          label={t("overview.statResources")}
          unit={t("overview.unitCount")}
          accent
        >
          <Link to="/resources" search={withScope(scope)}>
            {formatNumber(resourceTotal, locale)}
          </Link>
        </Stat>
        {scope === null && (
          <Stat label={t("overview.statApps")} unit={t("overview.unitCount")}>
            <Link to="/apps">{formatNumber(appsCount, locale)}</Link>
          </Stat>
        )}
        {scope?.kind !== "acct" && (
          <Stat
            label={t("overview.statAccounts")}
            unit={t("overview.unitCount")}
            hint={
              unreachable > 0 ? (
                <Link
                  to="/accounts"
                  search={{
                    f: { accessStatus: ["access_denied", "credential_error"] },
                  }}
                  className="text-warn hover:underline"
                >
                  {t("overview.inaccessible", { count: unreachable })}
                </Link>
              ) : undefined
            }
          >
            {scope === null ? (
              <Link to="/accounts">{formatNumber(accountsCount, locale)}</Link>
            ) : (
              formatNumber(accountsCount, locale)
            )}
          </Stat>
        )}
        <Stat label={t("overview.statTypes")} unit={t("overview.unitTypes")}>
          <Link to="/resources" search={withScope(scope)}>
            {formatNumber(typeTotal, locale)}
          </Link>
        </Stat>
        <Stat label={t("overview.statRegions")} unit={t("overview.unitCount")}>
          <Link to="/resources" search={withScope(scope)}>
            {formatNumber(regionTotal, locale)}
          </Link>
        </Stat>
      </dl>

      {scope?.kind === "app" && (
        <section
          className="flex flex-wrap items-center gap-2"
          aria-label={t("overview.appAccounts")}
        >
          <span className="text-xs text-muted-foreground">
            {t("overview.byAccount")}
          </span>
          {(appAccounts.data?.items ?? []).map((item) => (
            <Link
              key={item.accountId}
              to="/"
              search={scopeSearch("acct", item.accountId)}
              className="flex items-center gap-3 rounded-lg border bg-card px-3 py-2 hover:border-primary"
            >
              <strong className="font-semibold">{item.accountName}</strong>
              <span className="text-xs text-muted-foreground">
                {t(`env.${item.accountType}`)}
              </span>
            </Link>
          ))}
        </section>
      )}

      <details
        key={search.s ?? "all"}
        className="group flex flex-col"
        open={scope === null ? true : undefined}
      >
        <summary
          className={cn(
            "mb-4 w-fit cursor-pointer text-base font-semibold",
            scope === null && "hidden",
          )}
        >
          {t("overview.distributions")}
          {scope !== null && (
            <span className="ml-3 text-xs font-normal text-muted-foreground">
              {scope.kind === "acct"
                ? t("overview.distributionsHintAccount")
                : t("overview.distributionsHint")}
            </span>
          )}
        </summary>
        <div
          className={cn(
            "grid items-start gap-4 min-[801px]:grid-cols-2",
            scope?.kind === "acct"
              ? "min-[1301px]:grid-cols-2"
              : scope?.kind === "app"
                ? "min-[1301px]:grid-cols-4"
                : "min-[1301px]:grid-cols-3",
          )}
        >
          <Panel title={t("overview.byService")}>
            <Bars
              collapse={4}
              items={services.map((service) => ({
                key: service.key,
                label: service.label,
                count: service.count,
                icon: SERVICE_ICON[service.key],
                link: {
                  to: "/resources" as const,
                  search: serviceSearch(service.types),
                },
              }))}
            />
          </Panel>

          {scope?.kind === "app" && (
            <Panel title={t("overview.byAccount")}>
              <AccountBars
                accounts={(appAccounts.data?.items ?? [])
                  .filter((item) =>
                    matchingBuckets("accountId").some(
                      (bucket) => bucket.value === item.accountId,
                    ),
                  )
                  .map((item) => ({
                    ...item,
                    resourceCount:
                      facets.data?.accountId?.find(
                        (bucket) => bucket.value === item.accountId,
                      )?.count ?? 0,
                  }))}
                searchFor={(id) => scopeSearch("acct", id)}
              />
            </Panel>
          )}

          {scope?.kind !== "acct" && (
            <Panel title={t("overview.byEnv")}>
              <Bars
                collapse={4}
                items={byEnv.map((item) => ({
                  key: item.value,
                  label: item.label,
                  count: item.count,
                  active: selected("accountType", item.value),
                  onSelect: () => toggleFilter("accountType", item.value),
                }))}
              />
            </Panel>
          )}

          <Panel title={t("overview.byRegion")}>
            <Bars
              collapse={4}
              items={byRegion.map((item) => ({
                key: item.value,
                label: item.label,
                count: item.count,
                active: selected("awsRegion", item.value),
                onSelect: () => toggleFilter("awsRegion", item.value),
              }))}
            />
          </Panel>
        </div>
      </details>

      <OverviewResources
        state={state}
        onStateChange={setState}
        scope={scope}
        fallbackTotal={resourceTotal}
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

function Stat({
  label,
  unit,
  accent,
  hint,
  children,
}: {
  label: string
  unit?: string
  accent?: boolean
  hint?: ReactNode
  children: ReactNode
}) {
  return (
    <div className="min-w-0 border-l border-border bg-card px-5 py-3.5 first:border-l-0">
      <dt className="text-xs font-semibold text-muted-foreground">{label}</dt>
      <dd className="mt-1 text-xs text-muted-foreground">
        <strong
          className={cn(
            "mr-1 text-[28px] leading-tight font-semibold tracking-[-0.5px] text-foreground",
            accent && "text-primary",
          )}
        >
          {children}
        </strong>
        {unit}
      </dd>
      {hint && <dd className="mt-0.5 text-xs">{hint}</dd>}
    </div>
  )
}

type BarLink = {
  to: "/resources"
  search: TableSearch
}

type BarItem = {
  key: string
  label: string
  count: number
  icon?: ComponentType<{ className?: string }>
  link?: BarLink
  onSelect?: () => void
  active?: boolean
}

function Bars({ items, collapse }: { items: BarItem[]; collapse?: number }) {
  const { t, locale } = useI18n()
  const [expanded, setExpanded] = useState(false)
  if (items.length === 0) return <Empty />
  const max = Math.max(...items.map((item) => item.count))
  const hidden = collapse ? Math.max(0, items.length - collapse) : 0
  const shown = hidden > 0 && !expanded ? items.slice(0, collapse) : items
  const rowClass =
    "grid w-full grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 rounded-md px-2 py-1.5 text-left"
  return (
    <div className="flex flex-col">
      <ul className="flex flex-col">
        {shown.map((item) => {
          const Icon = item.icon
          const inner = (
            <>
              <span className="flex min-w-0 items-center gap-2 text-sm">
                {Icon && (
                  <Icon className="size-4 shrink-0 text-muted-foreground" />
                )}
                <span className="truncate">{item.label}</span>
              </span>
              <span className="text-sm tabular-nums">
                {formatNumber(item.count, locale)}
              </span>
              <Bar value={item.count} max={max} />
            </>
          )
          return (
            <li key={item.key}>
              {item.onSelect ? (
                <button
                  type="button"
                  aria-pressed={item.active}
                  onClick={item.onSelect}
                  className={cn(
                    rowClass,
                    item.active
                      ? "bg-primary-soft font-semibold text-primary"
                      : "hover:bg-subtle",
                  )}
                >
                  {inner}
                </button>
              ) : item.link ? (
                <Link
                  to={item.link.to}
                  search={item.link.search}
                  className={cn(rowClass, "hover:bg-subtle")}
                >
                  {inner}
                </Link>
              ) : (
                <div className={rowClass}>{inner}</div>
              )}
            </li>
          )
        })}
      </ul>
      {hidden > 0 && (
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          className="mt-1 w-fit px-2 text-sm font-medium text-primary hover:underline"
        >
          {expanded
            ? t("overview.showLess")
            : t("overview.showAll", { count: items.length })}
        </button>
      )}
    </div>
  )
}

function Bar({ value, max }: { value: number; max: number }) {
  return (
    <span className="col-span-2 mt-1 h-1 rounded-full bg-subtle-strong">
      <span
        className="block h-full rounded-full bg-primary"
        style={{ width: `${max > 0 ? (value / max) * 100 : 0}%` }}
      />
    </span>
  )
}

function AccountBars({
  accounts,
  searchFor,
}: {
  accounts: AccountSummary[]
  searchFor: (id: string) => TableSearch
}) {
  const { t, locale } = useI18n()
  const [expanded, setExpanded] = useState(false)
  const max = Math.max(0, ...accounts.map((account) => account.resourceCount))
  if (accounts.length === 0) return <Empty />
  const hidden = Math.max(0, accounts.length - 4)
  const shown = hidden > 0 && !expanded ? accounts.slice(0, 4) : accounts
  return (
    <div className="flex flex-col">
      <ul className="flex flex-col">
        {shown.map((account) => (
          <li key={account.accountId}>
            <Link
              to="/resources"
              search={searchFor(account.accountId)}
              className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 rounded-md px-2 py-1.5 hover:bg-subtle"
            >
              <span className="flex min-w-0 items-baseline gap-2">
                <span className="truncate text-sm">{account.accountName}</span>
                <span className="text-xs text-muted-foreground">
                  {t(`env.${account.accountType}`)}
                </span>
              </span>
              <span className="text-sm tabular-nums">
                {formatNumber(account.resourceCount, locale)}
              </span>
              <Bar value={account.resourceCount} max={max} />
            </Link>
          </li>
        ))}
      </ul>
      {hidden > 0 && (
        <button
          type="button"
          onClick={() => setExpanded((value) => !value)}
          className="mt-1 w-fit px-2 text-sm font-medium text-primary hover:underline"
        >
          {expanded
            ? t("overview.showLess")
            : t("overview.showAll", { count: accounts.length })}
        </button>
      )}
    </div>
  )
}

function Empty() {
  const { t } = useI18n()
  return (
    <p className="px-2 py-2 text-sm text-muted-foreground">
      {t("overview.empty")}
    </p>
  )
}

/** One bar per service: the type buckets of the menu's services, summed. */
function serviceBars(buckets: { value: string; count: number }[]) {
  const out = new Map<
    string,
    { key: string; label: string; types: string[]; count: number }
  >()
  for (const bucket of buckets) {
    const service = SERVICE_OF_TYPE.get(bucket.value)
    const key = service?.key ?? "other"
    const entry = out.get(key) ?? {
      key,
      label: service?.label ?? "Other",
      types: [],
      count: 0,
    }
    entry.types.push(bucket.value)
    entry.count += bucket.count
    out.set(key, entry)
  }
  return [...out.values()].sort((a, b) => b.count - a.count)
}
