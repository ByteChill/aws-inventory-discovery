import { createFileRoute, Link, useNavigate } from "@tanstack/react-router"
import { type ReactNode, useMemo } from "react"

import { useAccount, useCoverage, useResourceTypes } from "@/api/hooks"
import { useCreateScan } from "@/api/scans"
import type { AccountDetail } from "@/client"
import { InlineError, QueryRegion } from "@/components/inline-state"
import { Page, PageHeader, Section } from "@/components/layout/page"
import { Time } from "@/components/time"
import { Button } from "@/components/ui/button"
import { CoverageGrid } from "@/features/coverage-grid"
import { type MessageKey, useI18n } from "@/i18n"
import { errorText } from "@/lib/errors"
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
  const create = useCreateScan()
  const navigate = useNavigate()
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
      <PageHeader
        title={a.accountName}
        description={t("accounts.description", {
          app: a.eimApp ?? a.eimId ?? t("common.none"),
          env: t(`env.${a.accountType}`),
        })}
        actions={
          <>
            <Button asChild variant="outline">
              <Link to="/resources" search={{ s: `acct:${a.accountId}` }}>
                {t("accounts.viewResources")}
              </Link>
            </Button>
            <Button
              disabled={create.isPending}
              onClick={() =>
                create.mutate(
                  { spec: { accountIds: [a.accountId] } },
                  {
                    onSuccess: (scan) =>
                      navigate({
                        to: "/scans/$id",
                        params: { id: String(scan.id) },
                      }),
                  },
                )
              }
            >
              {t("accounts.scanThis")}
            </Button>
          </>
        }
      />
      {create.error && <InlineError message={errorText(t, create.error)} />}

      <section className="rounded-lg border bg-card p-4">
        <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4 xl:grid-cols-7">
          <Field label={t("accounts.column.id")}>
            <span className="font-mono">{a.accountId}</span>
          </Field>
          <Field label={t("detail.env")}>{t(`env.${a.accountType}`)}</Field>
          <Field label="EIM">
            {a.eimId ? (
              <>
                <Link
                  to="/"
                  search={{ s: `app:${a.eimId}` }}
                  className="text-primary hover:underline"
                >
                  {a.eimApp ?? a.eimId}
                </Link>
                {a.eimApp && (
                  <span className="mt-0.5 block font-mono text-xs text-muted-foreground">
                    {a.eimId}
                  </span>
                )}
              </>
            ) : (
              <span className="text-muted-foreground">{t("common.none")}</span>
            )}
          </Field>
          <Field label="BA">
            <span className="font-mono">{a.baId ?? t("common.none")}</span>
          </Field>
          <Field label={t("accounts.column.status")}>
            {a.accessStatus === "ok" ? (
              t("access.ok")
            ) : (
              <span className="text-warn">
                {t(`access.${a.accessStatus}` as MessageKey)}
              </span>
            )}
            {purgeDays !== null && (
              <span className="mt-0.5 block text-xs text-muted-foreground">
                {t("account.purgeIn", { days: purgeDays })}
              </span>
            )}
          </Field>
          <Field label={t("accounts.column.resources")}>
            <Link
              to="/resources"
              search={{ s: `acct:${a.accountId}` }}
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
      </section>

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
    <div>
      <dt className="text-xs font-semibold text-muted-foreground">{label}</dt>
      <dd className="mt-1 min-w-0">{children}</dd>
    </div>
  )
}
