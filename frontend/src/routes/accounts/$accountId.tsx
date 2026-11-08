import { createFileRoute, Link } from "@tanstack/react-router"
import { type ReactNode, useMemo } from "react"

import { useAccount, useCoverage, useResourceTypes } from "@/api/hooks"
import type { AccountDetail } from "@/client"
import { QueryRegion } from "@/components/inline-state"
import { Page, PageHeader, Section } from "@/components/layout/page"
import { Time } from "@/components/time"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { CoverageGrid } from "@/features/coverage-grid"
import { type MessageKey, useI18n } from "@/i18n"
import { formatCount } from "@/lib/format"

export const Route = createFileRoute("/accounts/$accountId")({
  component: AccountPage,
})

const DAY = 86_400_000

function AccountPage() {
  const { t } = useI18n()
  const { accountId } = Route.useParams()
  const query = useAccount(accountId)
  return (
    <Page>
      <QueryRegion
        isLoading={query.isLoading}
        error={query.error}
        onRetry={() => query.refetch()}
        isEmpty={!query.data}
        emptyMessage={t("error.account_not_found")}
      >
        {query.data && <Detail account={query.data} />}
      </QueryRegion>
    </Page>
  )
}

function Detail({ account: a }: { account: AccountDetail }) {
  const { t } = useI18n()
  const coverage = useCoverage(a.accountId)
  const types = useResourceTypes()
  const typeLabels = useMemo(
    () => new Map(types.data?.map((x) => [x.typeName, x.label])),
    [types.data],
  )

  const scanned = useMemo(
    () => new Set(coverage.data?.map((c) => c.region)),
    [coverage.data],
  )
  const scannedRegions = [...scanned].sort()
  // Regions that are on for the account but have no scan results yet.
  const idleRegions = a.regions
    .filter((r) => r.reachable && !scanned.has(r.region))
    .map((r) => r.region)
    .sort()

  const purgeDays = a.purgeAt
    ? Math.max(0, Math.ceil((new Date(a.purgeAt).getTime() - Date.now()) / DAY))
    : null

  return (
    <>
      <div className="flex flex-col gap-2">
        <PageHeader
          title={a.accountName}
          actions={
            // Scanning needs the write endpoint (next milestone).
            <Button variant="outline" disabled title={t("common.comingSoon")}>
              {t("action.scan")}
            </Button>
          }
        />
        {(a.accessStatus !== "ok" || purgeDays !== null) && (
          <div className="flex flex-wrap gap-2">
            {a.accessStatus !== "ok" && (
              <Badge tone="danger">
                {t(`access.${a.accessStatus}` as MessageKey)}
              </Badge>
            )}
            {purgeDays !== null && (
              <Badge>{t("account.purgeIn", { days: purgeDays })}</Badge>
            )}
          </div>
        )}
      </div>

      <Section title={t("accounts.basic")}>
        <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1.5">
          <Field label={t("accounts.column.id")}>
            <span className="font-mono">{a.accountId}</span>
          </Field>
          <Field label={t("detail.env")}>{t(`env.${a.accountType}`)}</Field>
          {a.eimId && (
            <Field label={t("detail.eim")}>
              <span className="font-mono">{a.eimId}</span>
              {a.eimApp && ` ${a.eimApp}`}
            </Field>
          )}
          {a.baId && (
            <Field label={t("detail.ba")}>
              <span className="font-mono">{a.baId}</span>
            </Field>
          )}
          <Field label={t("accounts.column.resources")}>
            <Link
              to="/resources"
              search={{ f: { accountId: [a.accountId] } }}
              className="text-primary hover:underline"
            >
              {formatCount(t, "resources", a.resourceCount)}
            </Link>
          </Field>
          {a.lastScanOkAt && (
            <Field label={t("accounts.lastScanOk")}>
              <Time value={a.lastScanOkAt} />
            </Field>
          )}
        </dl>
      </Section>

      <Section title={t("accounts.regions")}>
        <QueryRegion
          isLoading={coverage.isLoading}
          error={coverage.error}
          onRetry={() => coverage.refetch()}
          isEmpty={scannedRegions.length === 0 && idleRegions.length === 0}
          emptyMessage={t("accounts.noCoverage")}
        >
          <div className="flex flex-col gap-3">
            {scannedRegions.length > 0 && (
              <RegionList regions={scannedRegions} />
            )}
            {idleRegions.length > 0 && (
              <div className="flex flex-col gap-1">
                <h3>{t("accounts.regionsEnabled")}</h3>
                <RegionList regions={idleRegions} />
              </div>
            )}
          </div>
        </QueryRegion>
      </Section>

      <Section title={t("accounts.matrix")}>
        <QueryRegion
          isLoading={coverage.isLoading}
          error={coverage.error}
          onRetry={() => coverage.refetch()}
          isEmpty={!coverage.data?.length}
          emptyMessage={t("accounts.noCoverage")}
        >
          {coverage.data && (
            <CoverageGrid cells={coverage.data} typeLabels={typeLabels} />
          )}
        </QueryRegion>
      </Section>
    </>
  )
}

function RegionList({ regions }: { regions: string[] }) {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 font-mono">
      {regions.map((r) => (
        <li key={r}>{r}</li>
      ))}
    </ul>
  )
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0">{children}</dd>
    </>
  )
}
