import { createFileRoute, Link, useLocation } from "@tanstack/react-router"
import { type ReactNode, useDeferredValue, useMemo, useState } from "react"
import { z } from "zod"

import {
  useAccount,
  useAccounts,
  useApplication,
  useApplications,
  useFacets,
  useOverview,
  useResources,
} from "@/api/hooks"
import type { AccountSummary, Application, ResourceItem } from "@/client"
import { CopyButton } from "@/components/copy-button"
import { InlineError } from "@/components/inline-state"
import { Page } from "@/components/layout/page"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { NAV_GROUPS } from "@/features/inventory-nav"
import { type MessageKey, useI18n } from "@/i18n"
import { errorText } from "@/lib/errors"
import { formatNumber } from "@/lib/format"
import { parseScope, type Scope, scopeParams, withScope } from "@/lib/scope"
import { cn } from "@/lib/utils"

export const Route = createFileRoute("/")({
  validateSearch: z.object({ s: z.string().optional().catch(undefined) }),
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
  const location = useLocation()
  const scope = parseScope(search.s)
  const appEim = scope?.kind === "app" ? scope.eim : undefined
  const accountId = scope?.kind === "acct" ? scope.id : undefined

  const overview = useOverview()
  const facets = useFacets(
    ["resourceType", "accountType", "awsRegion"],
    scopeParams(scope),
  )
  const app = useApplication(appEim)
  const account = useAccount(accountId)
  const appAccounts = useAccounts(
    appEim ? { eimId: [appEim] } : {},
    { page: 1, pageSize: 8, sort: "-resourceCount" },
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

  const [appQuery, setAppQuery] = useState("")
  const appSearch = useDeferredValue(appQuery.trim())
  const appMatches = useApplications(
    { q: appSearch || undefined },
    { page: 1, pageSize: 5, sort: "-resourceCount" },
  )

  const recent = useResources(scopeParams(scope), {
    page: 1,
    pageSize: 5,
    sort: "-lastChangedAt",
  })

  const byType = facets.data?.resourceType ?? []
  const byEnv = facets.data?.accountType ?? []
  const byRegion = facets.data?.awsRegion ?? []
  const resourceTotal = byType.reduce((sum, item) => sum + item.count, 0)
  const typeTotal = byType.length
  const regionTotal = byRegion.length
  const typeLabels = useMemo(
    () => new Map(byType.map((item) => [item.value, item.label])),
    [byType],
  )
  const services = useMemo(() => serviceBars(byType), [byType])

  const appsCount = scope === null ? (appsTotal.data?.total ?? 0) : 0
  const accountsCount =
    scope === null
      ? (overview.data?.accountTotal ?? 0)
      : scope.kind === "app"
        ? (appAccounts.data?.total ?? app.data?.accountCount ?? 0)
        : 1
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

  const back = `${location.pathname}${location.searchStr}`

  return (
    <Page className="gap-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="min-w-0">
          <h1 className="break-all">{title}</h1>
          <p className="mt-1 text-muted-foreground">
            {scope === null
              ? t("overview.allSubtitle")
              : scope.kind === "app"
                ? t("overview.appSubtitle", {
                    eim: scope.eim,
                    ba: app.data?.baId ?? t("common.none"),
                    accounts: accountsCount,
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
                  })}
          </p>
        </div>
        {scope && (
          <Button variant="outline" asChild>
            <Link to="/">{t("overview.allScope")}</Link>
          </Button>
        )}
      </header>

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

      <details className="flex flex-col border-t pt-4">
        <summary className="cursor-pointer font-semibold">
          {t("overview.distributions")}
          <span className="ml-3 text-xs font-normal text-muted-foreground">
            {scope?.kind === "acct"
              ? t("overview.distributionsHintAccount")
              : t("overview.distributionsHint")}
          </span>
        </summary>
        <div
          className={cn(
            "mt-4 grid gap-x-8 gap-y-6 md:grid-cols-2",
            scope?.kind === "acct" ? "xl:grid-cols-2" : "xl:grid-cols-4",
          )}
        >
          <Panel title={t("overview.byService")}>
            <Bars
              items={services.map((service) => ({
                key: service.key,
                label: service.label,
                count: service.count,
                link: {
                  to: "/resources" as const,
                  search: withScope(scope, {
                    f: { resourceType: service.types },
                  }),
                },
              }))}
            />
          </Panel>

          {scope === null && (
            <Panel title={t("overview.byApp")}>
              <Input
                value={appQuery}
                onChange={(e) => setAppQuery(e.target.value)}
                placeholder={t("overview.appSearch")}
                aria-label={t("overview.appSearch")}
                className="mb-2"
              />
              <AppBars
                apps={appMatches.data?.items ?? []}
                total={appMatches.data?.total ?? 0}
                query={appSearch}
              />
            </Panel>
          )}

          {scope?.kind === "app" && (
            <Panel title={t("overview.byAccount")}>
              <AccountBars
                accounts={appAccounts.data?.items ?? []}
                scope={scope}
              />
            </Panel>
          )}

          {scope?.kind !== "acct" && (
            <Panel title={t("overview.byEnv")}>
              <Bars
                items={byEnv.map((item) => ({
                  key: item.value,
                  label: item.label,
                  count: item.count,
                  link: {
                    to: "/resources" as const,
                    search: withScope(scope, {
                      f: { accountType: [item.value] },
                    }),
                  },
                }))}
              />
            </Panel>
          )}

          <Panel title={t("overview.byRegion")}>
            <Bars
              items={byRegion.map((item) => ({
                key: item.value,
                label: item.label,
                count: item.count,
                link: {
                  to: "/resources" as const,
                  search: withScope(scope, {
                    f: { awsRegion: [item.value] },
                  }),
                },
              }))}
            />
          </Panel>
        </div>
      </details>

      <section className="flex flex-col gap-3 border-t pt-4">
        <div className="flex flex-wrap items-baseline justify-between gap-2">
          <h2>
            {t("overview.recent")}{" "}
            <span className="text-sm font-normal text-muted-foreground">
              {formatNumber(resourceTotal, locale)} {t("overview.unitCount")}
            </span>
          </h2>
          <Link
            to="/resources"
            search={withScope(scope)}
            className="text-sm text-primary hover:underline"
          >
            {t("overview.viewAllResources")}
          </Link>
        </div>
        {recent.error ? (
          <InlineError
            message={errorText(t, recent.error)}
            onRetry={() => recent.refetch()}
          />
        ) : recent.isLoading ? (
          <p className="text-muted-foreground">{t("common.loading")}</p>
        ) : (
          <RecentResources
            rows={recent.data?.items ?? []}
            global={scope === null}
            back={back}
            typeLabels={typeLabels}
          />
        )}
      </section>
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
            "mr-0.5 text-lg leading-tight font-semibold text-foreground",
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

function Panel({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex min-w-0 flex-col">
      <h3 className="mb-1">{title}</h3>
      {children}
    </section>
  )
}

type BarLink = {
  to: "/resources"
  search: ReturnType<typeof withScope>
}

function Bars({
  items,
}: {
  items: { key: string; label: string; count: number; link: BarLink }[]
}) {
  const { locale } = useI18n()
  if (items.length === 0) return <Empty />
  const max = Math.max(...items.map((item) => item.count))
  return (
    <ul className="flex flex-col">
      {items.map((item) => (
        <li key={item.key}>
          <Link
            to={item.link.to}
            search={item.link.search}
            className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 rounded-md px-2 py-1.5 hover:bg-subtle"
          >
            <span className="truncate text-sm">{item.label}</span>
            <span className="text-sm tabular-nums">
              {formatNumber(item.count, locale)}
            </span>
            <Bar value={item.count} max={max} />
          </Link>
        </li>
      ))}
    </ul>
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

function AppBars({
  apps,
  total,
  query,
}: {
  apps: Application[]
  total: number
  query: string
}) {
  const { t, locale } = useI18n()
  const max = Math.max(0, ...apps.map((app) => app.resourceCount))
  return (
    <div className="flex flex-col">
      {apps.length === 0 ? (
        <Empty />
      ) : (
        <ul className="flex flex-col">
          {apps.map((app) => (
            <li key={app.eimId}>
              <Link
                to="/resources"
                search={{ s: `app:${app.eimId}` }}
                className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-x-3 rounded-md px-2 py-1.5 hover:bg-subtle"
              >
                <span className="flex min-w-0 items-baseline gap-2">
                  <span className="truncate text-sm">{app.eimApp}</span>
                  <span className="shrink-0 text-xs text-muted-foreground">
                    {app.eimId}
                  </span>
                </span>
                <span className="text-sm tabular-nums">
                  {formatNumber(app.resourceCount, locale)}
                </span>
                <Bar value={app.resourceCount} max={max} />
              </Link>
            </li>
          ))}
        </ul>
      )}
      <Link
        to="/apps"
        search={query ? { q: query } : {}}
        className="mt-1 px-2 text-sm text-primary hover:underline"
      >
        {query
          ? t("overview.viewMatchingApps", { count: total })
          : t("overview.viewAllApps", { count: total })}
      </Link>
    </div>
  )
}

function AccountBars({
  accounts,
  scope,
}: {
  accounts: AccountSummary[]
  scope: Scope
}) {
  const { t, locale } = useI18n()
  const max = Math.max(0, ...accounts.map((account) => account.resourceCount))
  if (accounts.length === 0) return <Empty />
  return (
    <ul className="flex flex-col">
      {accounts.map((account) => (
        <li key={account.accountId}>
          <Link
            to="/resources"
            search={withScope(scope, {
              f: { accountId: [account.accountId] },
            })}
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
  )
}

function RecentResources({
  rows,
  global,
  back,
  typeLabels,
}: {
  rows: ResourceItem[]
  global: boolean
  back: string
  typeLabels: Map<string, string>
}) {
  const { t } = useI18n()
  if (rows.length === 0) return <Empty />
  return (
    <ul className="divide-y rounded-lg border bg-card">
      {rows.map((row) => (
        <li key={row.id}>
          <Link
            to="/resources/$id"
            params={{ id: String(row.id) }}
            search={{ back }}
            className={cn(
              "grid grid-cols-1 items-center gap-x-5 gap-y-1 px-4 py-2.5 hover:bg-subtle",
              global
                ? "sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_auto] md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_minmax(0,1fr)_auto]"
                : "sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_auto]",
            )}
          >
            <span className="min-w-0">
              <span className="flex min-w-0 items-center gap-1.5">
                <span className="truncate font-semibold text-primary">
                  {row.resourceName ?? row.resourceId}
                </span>
                {row.status !== "active" && (
                  <Badge tone={row.status === "missing" ? "warn" : "neutral"}>
                    {t(`status.${row.status}`)}
                  </Badge>
                )}
                {row.stale && <Badge>{t("status.stale")}</Badge>}
              </span>
              <span className="block truncate font-mono text-xs text-muted-foreground">
                {row.resourceId}
              </span>
            </span>
            <span className="min-w-0">
              <span className="block truncate text-sm">
                {row.account.accountName}
              </span>
              <span className="flex min-w-0 items-center gap-0.5 font-mono text-xs text-muted-foreground">
                <span className="truncate">{row.account.accountId}</span>
                <CopyButton
                  value={row.account.accountId}
                  className="size-5 shrink-0"
                />
              </span>
            </span>
            {global && (
              <span className="hidden min-w-0 md:block">
                <span className="block truncate text-sm">
                  {row.account.eimApp ?? ""}
                </span>
                <span className="block truncate font-mono text-xs text-muted-foreground">
                  {row.account.eimId}
                </span>
              </span>
            )}
            <span className="min-w-0 text-sm text-muted-foreground sm:text-right">
              <span className="block truncate">
                {row.awsRegion === "global"
                  ? t("region.global")
                  : row.awsRegion}
              </span>
              <span className="block truncate text-xs">
                {typeLabels.get(row.resourceType) ?? row.resourceType}
              </span>
            </span>
          </Link>
        </li>
      ))}
    </ul>
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
