import { Link, useLocation, useNavigate } from "@tanstack/react-router"
import {
  Boxes,
  ChevronDown,
  ChevronRight,
  Cloud,
  Container,
  Cpu,
  Globe,
  Inbox,
  KeyRound,
  LayoutDashboard,
  Megaphone,
  PanelLeftClose,
  ScanSearch,
  Split,
  Zap,
} from "lucide-react"
import { type ComponentType, useMemo, useState } from "react"

import {
  useAccount,
  useApplication,
  useApplications,
  useFacets,
  useOverview,
  useResourceTypes,
} from "@/api/hooks"
import { useScanSummary } from "@/api/scans"
import {
  buildNav,
  type GroupKey,
  type NavGroup,
  type NavService,
} from "@/features/inventory-nav"
import { useI18n } from "@/i18n"
import { formatNumber } from "@/lib/format"
import { parseScope, type Scope, scopeParams, scopeValue } from "@/lib/scope"
import { cn } from "@/lib/utils"
import { ScopeDialog } from "./scope-dialog"

const SERVICE_ICON: Record<string, ComponentType<{ className?: string }>> = {
  ec2: Cpu,
  ecs: Container,
  lambda: Zap,
  vpc: Cloud,
  elb: Split,
  route53: Globe,
  sqs: Inbox,
  sns: Megaphone,
  secretsmanager: KeyRound,
  other: Boxes,
}

const linkBase =
  "flex min-h-8 items-center gap-1.5 rounded-md px-2.5 py-1 hover:bg-subtle-strong"
const linkActive = "bg-card font-semibold text-primary hover:bg-card"

export function Sidebar({
  open,
  onClose,
}: {
  open: boolean
  onClose: () => void
}) {
  const { t, locale } = useI18n()
  const location = useLocation()
  const navigate = useNavigate() as unknown as (opts: {
    to: string
    search?: Record<string, unknown>
    replace?: boolean
  }) => void
  const [scopeOpen, setScopeOpen] = useState(false)
  const [collapsed, setCollapsed] = useState<Set<GroupKey>>(new Set())

  const types = useResourceTypes()
  const summary = useScanSummary()
  const scope = parseScope((location.search as { s?: string }).s)
  const facets = useFacets(["resourceType"], scopeParams(scope))
  const allApps = useApplications({}, { page: 1, pageSize: 1 })
  const overview = useOverview()
  const app = useApplication(scope?.kind === "app" ? scope.eim : undefined)
  const account = useAccount(scope?.kind === "acct" ? scope.id : undefined)

  const groups = useMemo(() => buildNav(types.data ?? []), [types.data])
  const counts = useMemo(() => {
    const map = new Map<string, number>()
    for (const bucket of facets.data?.resourceType ?? [])
      map.set(bucket.value, bucket.count)
    return map
  }, [facets.data])

  const currentTypes = useMemo(() => {
    const search = location.search as {
      f?: { resourceType?: string | string[] }
    }
    const value = search.f?.resourceType
    if (Array.isArray(value)) return value
    return typeof value === "string" && value ? [value] : []
  }, [location.search])

  const activeService =
    location.pathname === "/resources" && currentTypes.length === 1
      ? groups
          .flatMap((g) => g.services)
          .find((s) => s.types.includes(currentTypes[0]))
      : undefined
  const activeGroup = activeService
    ? groups.find((g) => g.services.includes(activeService))
    : undefined
  const groupActive = (group: NavGroup) =>
    !activeService &&
    currentTypes.length > 0 &&
    currentTypes.every((type) =>
      group.services.some((s) => s.types.includes(type)),
    )

  const overviewActive =
    location.pathname === "/" ||
    location.pathname === "/apps" ||
    location.pathname.startsWith("/accounts")
  const scanActive =
    location.pathname.startsWith("/scans") ||
    location.pathname.startsWith("/tasks") ||
    location.pathname.startsWith("/scan-scopes")
  const running = summary.data?.sweep ? 1 : 0
  const total = [...counts.values()].reduce((a, b) => a + b, 0)

  const scopeTitle =
    scope === null
      ? t("scope.all")
      : scope.kind === "app"
        ? (app.data?.eimApp ?? scope.eim)
        : (account.data?.accountName ?? scope.id)
  const scopeSubtitle =
    scope === null
      ? allApps.data && overview.data
        ? t("scope.allSummary", {
            apps: allApps.data.total,
            accounts: overview.data.accountTotal,
          })
        : null
      : scope.kind === "app"
        ? t("scope.appLabel", { eim: scope.eim })
        : t("scope.accountLabel", { id: scope.id })

  function selectScope(next: Scope | null) {
    const value = scopeValue(next)
    const search = { ...(location.search as Record<string, unknown>) }
    if (value) search.s = value
    else delete search.s
    if (location.pathname === "/" || location.pathname === "/resources") {
      navigate({ to: location.pathname, search, replace: true })
    } else {
      navigate({ to: "/", search: value ? { s: value } : {} })
    }
    setScopeOpen(false)
    onClose()
  }

  function toggleGroup(key: GroupKey) {
    setCollapsed((current) => {
      const next = new Set(current)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }

  return (
    <>
      {open && (
        <button
          type="button"
          aria-label={t("nav.close")}
          onClick={onClose}
          className="fixed inset-0 z-40 bg-black/25 min-[801px]:hidden"
        />
      )}
      <aside
        className={cn(
          "fixed inset-y-0 left-0 z-50 flex w-[264px] flex-col border-r border-border bg-sidebar transition-transform max-[1180px]:w-[224px] max-[800px]:w-[min(280px,85vw)] max-[800px]:shadow-pop",
          open ? "translate-x-0" : "max-[800px]:-translate-x-full",
        )}
        aria-label={t("nav.main")}
      >
        <div className="flex items-center gap-2.5 px-[22px] pt-[18px] pb-3.5 font-semibold max-[1180px]:px-4">
          <span
            aria-hidden="true"
            className="grid grid-cols-2 gap-[3px] text-primary"
          >
            <i className="size-[7px] rounded-[2px] bg-current" />
            <i className="size-[7px] rounded-[2px] bg-current" />
            <i className="size-[7px] rounded-[2px] bg-current" />
            <i className="size-[7px] rounded-[2px] bg-current opacity-40" />
          </span>
          <span className="min-w-0 truncate">{t("app.name")}</span>
          <button
            type="button"
            onClick={onClose}
            aria-label={t("nav.close")}
            className="ml-auto grid size-8 place-items-center rounded-md text-muted-foreground hover:bg-subtle-strong min-[801px]:hidden"
          >
            <PanelLeftClose className="size-4" />
          </button>
        </div>

        <div className="px-3 pb-2.5">
          <button
            type="button"
            onClick={() => setScopeOpen(true)}
            aria-haspopup="dialog"
            aria-label={t("scope.switch")}
            title={t("scope.switch")}
            className={cn(
              "grid min-h-[50px] w-full grid-cols-[32px_minmax(0,1fr)_14px] items-center gap-2 rounded-md border border-[#d8d0c8] bg-card px-2 py-2 text-left hover:border-input dark:border-border",
              scope && "border-primary bg-primary-soft hover:border-primary",
            )}
          >
            <span
              aria-hidden="true"
              className={cn(
                "grid size-8 place-items-center rounded-md font-semibold",
                scope
                  ? "bg-primary-soft text-primary"
                  : "bg-subtle-strong text-muted-foreground",
              )}
            >
              {scope ? (
                scopeTitle.slice(0, 1).toUpperCase()
              ) : (
                <LayoutDashboard className="size-4" />
              )}
            </span>
            <span className="min-w-0">
              <strong
                className={cn(
                  "block truncate font-semibold",
                  scope && "text-primary",
                )}
              >
                {scopeTitle}
              </strong>
              {scopeSubtitle && (
                <span className="block truncate text-xs text-muted-foreground">
                  {scopeSubtitle}
                </span>
              )}
            </span>
            <ChevronDown className="size-3.5 text-muted-foreground" />
          </button>
        </div>

        <nav
          className="flex-1 overflow-auto px-2.5 pb-4"
          aria-label={t("nav.main")}
        >
          <p className="px-3 pt-3 pb-1.5 text-xs font-semibold text-muted-foreground">
            {t("nav.inventory")}
          </p>
          <Link
            to="/"
            search={scopeValue(scope) ? { s: scopeValue(scope) } : {}}
            onClick={onClose}
            aria-current={overviewActive ? "page" : undefined}
            className={cn(
              linkBase,
              "gap-1.5 font-semibold",
              overviewActive && linkActive,
            )}
          >
            <LayoutDashboard className="size-4 text-muted-foreground" />
            {t("nav.overview")}
            <span className="ml-auto text-xs font-normal text-muted-foreground">
              {formatNumber(total, locale)}
            </span>
          </Link>

          {groups.map((group) => {
            const isCollapsed = collapsed.has(group.key)
            const isOn = activeGroup?.key === group.key || groupActive(group)
            return (
              <div key={group.key} className="mt-1">
                <div className="flex items-center gap-0.5 pl-1">
                  <button
                    type="button"
                    onClick={() => toggleGroup(group.key)}
                    aria-expanded={!isCollapsed}
                    aria-label={`${isCollapsed ? t("nav.expand") : t("nav.collapse")} ${t(`nav.group.${group.key}`)}`}
                    className="grid size-[22px] shrink-0 place-items-center rounded-sm text-muted-foreground hover:bg-subtle-strong"
                  >
                    {isCollapsed ? (
                      <ChevronRight className="size-3.5" />
                    ) : (
                      <ChevronDown className="size-3.5" />
                    )}
                  </button>
                  <Link
                    to="/resources"
                    search={{
                      ...(scopeValue(scope) ? { s: scopeValue(scope) } : {}),
                      f: {
                        resourceType: group.services.flatMap((s) => s.types),
                      },
                    }}
                    onClick={onClose}
                    className={cn(
                      "min-h-7 rounded-md px-2 py-0.5 text-xs font-semibold text-muted-foreground hover:bg-subtle-strong",
                      isOn && "bg-card text-primary hover:bg-card",
                    )}
                  >
                    {t(`nav.group.${group.key}`)}
                  </Link>
                </div>
                {!isCollapsed &&
                  group.services.map((service) => (
                    <ServiceLink
                      key={service.key}
                      service={service}
                      scope={scope}
                      active={activeService?.key === service.key}
                      count={service.types.reduce(
                        (sum, type) => sum + (counts.get(type) ?? 0),
                        0,
                      )}
                      onClick={onClose}
                    />
                  ))}
              </div>
            )
          })}

          <div className="mt-4 border-t pt-3">
            <Link
              to="/scans"
              onClick={onClose}
              aria-current={scanActive ? "page" : undefined}
              className={cn(
                linkBase,
                "font-semibold",
                scanActive && linkActive,
              )}
            >
              <ScanSearch className="size-4 text-muted-foreground" />
              {t("nav.scanManagement")}
              {running > 0 && (
                <span className="ml-auto text-xs font-normal text-muted-foreground">
                  {t("nav.runningScans", { count: running })}
                </span>
              )}
            </Link>
          </div>
        </nav>

        <p className="border-t px-[22px] py-3 text-xs text-muted-foreground max-[1180px]:px-4">
          {t("nav.readOnly")}
        </p>
      </aside>

      {scopeOpen && (
        <ScopeDialog
          current={scope}
          onSelect={selectScope}
          onClose={() => setScopeOpen(false)}
        />
      )}
    </>
  )
}

function ServiceLink({
  service,
  scope,
  active,
  count,
  onClick,
}: {
  service: NavService
  scope: Scope | null
  active: boolean
  count: number
  onClick: () => void
}) {
  const { locale } = useI18n()
  const Icon = SERVICE_ICON[service.key] ?? Boxes
  return (
    <Link
      to="/resources"
      search={{
        ...(scopeValue(scope) ? { s: scopeValue(scope) } : {}),
        f: { resourceType: [service.types[0]] },
      }}
      onClick={onClick}
      aria-current={active ? "page" : undefined}
      className={cn(linkBase, "pl-[34px]", active && linkActive)}
    >
      <Icon className="size-4 text-muted-foreground" />
      <span className="truncate">{service.label}</span>
      <span className="ml-auto text-xs font-normal text-muted-foreground">
        {formatNumber(count, locale)}
      </span>
    </Link>
  )
}
