import { createFileRoute, Link } from "@tanstack/react-router"
import { type ReactNode, useMemo, useState } from "react"

import {
  useAccount,
  useCoverage,
  useFacets,
  useResourceTypes,
} from "@/api/hooks"
import type { AccountDetail } from "@/client"
import { QueryRegion } from "@/components/inline-state"
import { Breadcrumb } from "@/components/layout/crumbs"
import { Page, PageHeader, Panel } from "@/components/layout/page"
import { Time } from "@/components/time"
import { Button } from "@/components/ui/button"
import { CoverageGrid } from "@/features/coverage-grid"
import { ScanDialog } from "@/features/scan-dialog"
import { type MessageKey, useI18n } from "@/i18n"
import { formatNumber } from "@/lib/format"

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
  const { t, locale } = useI18n()
  const [scanOpen, setScanOpen] = useState(false)
  const scanPreset = useMemo(
    () => ({ accountIds: [a.accountId] }),
    [a.accountId],
  )
  const coverage = useCoverage(a.accountId)
  const facets = useFacets(["awsRegion"], { accountId: [a.accountId] })
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
  const regionCounts = new Map(
    (facets.data?.awsRegion ?? []).map((item) => [item.value, item.count]),
  )
  const coverageRows = coverage.data ?? []
  const problems = coverageRows.filter((cell) => cell.state !== "ok")

  return (
    <>
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
          {
            key: "accounts",
            content: <Link to="/accounts">{t("accounts.title")}</Link>,
          },
          {
            key: "account",
            content: (
              <Link to="/" search={{ s: `acct:${a.accountId}` }}>
                {a.accountName}
              </Link>
            ),
          },
          {
            key: "coverage",
            content: t("accounts.coverageTitle", { name: a.accountName }),
          },
        ]}
      />
      <PageHeader
        title={t("accounts.coverageTitle", { name: a.accountName })}
        description={t("accounts.coverageSubtitle", {
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
            <Button aria-haspopup="dialog" onClick={() => setScanOpen(true)}>
              {t("accounts.scanThis")}
            </Button>
          </>
        }
      />
      <ScanDialog
        open={scanOpen}
        onClose={() => setScanOpen(false)}
        preset={scanPreset}
      />
      {a.accessStatus !== "ok" && (
        <section className="rounded-lg border border-warn/30 bg-warn-soft px-4 py-3">
          <h2 className="text-warn">
            {t(`access.${a.accessStatus}` as MessageKey)}
          </h2>
          <p className="mt-1 text-muted-foreground">
            {a.accessError ?? t("accounts.accessNotice" as MessageKey)}
          </p>
        </section>
      )}

      <Panel>
        <dl className="grid grid-cols-2 gap-4 lg:grid-cols-3 xl:grid-cols-6">
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
              {formatNumber(a.resourceCount, locale)}
            </Link>
          </Field>
        </dl>
      </Panel>

      <div className="grid items-start gap-4 lg:grid-cols-2">
        <Panel title={t("accounts.coverage")}>
          <QueryRegion
            isLoading={coverage.isLoading}
            error={coverage.error}
            onRetry={() => coverage.refetch()}
            isEmpty={coverageRows.length === 0}
            emptyMessage={t("accounts.noCoverage")}
          >
            {problems.length > 0 ? (
              <>
                <p className="mb-2 text-xs text-muted-foreground">
                  {t("accounts.problemScopes")}
                </p>
                <div className="overflow-auto">
                  <table className="w-full text-left">
                    <thead>
                      <tr className="border-b text-xs text-muted-foreground">
                        <th className="py-1.5 pr-3 font-medium">
                          {t("resources.group.resourceType")}
                        </th>
                        <th className="py-1.5 pr-3 font-medium">
                          {t("resources.group.awsRegion")}
                        </th>
                        <th className="py-1.5 pr-3 font-medium">
                          {t("accounts.column.status")}
                        </th>
                        <th className="py-1.5 font-medium">
                          {t("accounts.lastScanOk")}
                        </th>
                      </tr>
                    </thead>
                    <tbody>
                      {problems.map((cell) => (
                        <tr
                          key={`${cell.region}|${cell.resourceType}`}
                          className="border-b last:border-b-0"
                        >
                          <td className="py-2 pr-3">
                            {typeLabels.get(cell.resourceType) ??
                              cell.resourceType}
                          </td>
                          <td className="py-2 pr-3 font-mono text-xs">
                            {cell.region}
                          </td>
                          <td className="py-2 pr-3 text-warn">
                            {t(
                              `accounts.cell.${cell.state === "access_denied" ? "denied" : cell.state}` as MessageKey,
                            )}
                          </td>
                          <td className="py-2 text-muted-foreground">
                            {cell.lastSucceededAt ? (
                              <Time value={cell.lastSucceededAt} />
                            ) : (
                              t("common.none")
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
                {problems.length < coverageRows.length &&
                  idleRegions.length === 0 && (
                    <p className="mt-2 text-xs text-muted-foreground">
                      {t("accounts.coverageFresh")}
                    </p>
                  )}
              </>
            ) : (
              <p>
                {t(
                  idleRegions.length
                    ? "accounts.regionsEnabled"
                    : "accounts.coverageOk",
                )}
              </p>
            )}
          </QueryRegion>
        </Panel>

        <Panel title={t("accounts.regions")}>
          <QueryRegion
            isLoading={coverage.isLoading}
            error={coverage.error}
            onRetry={() => coverage.refetch()}
            isEmpty={scannedRegions.length === 0 && idleRegions.length === 0}
            emptyMessage={t("accounts.noCoverage")}
          >
            <div className="overflow-auto">
              <table className="w-full text-left">
                <thead>
                  <tr className="border-b text-xs text-muted-foreground">
                    <th className="py-1.5 pr-3 font-medium">
                      {t("resources.group.awsRegion")}
                    </th>
                    <th className="py-1.5 pr-3 font-medium">
                      {t("accounts.readable")}
                    </th>
                    <th className="py-1.5 pr-3 font-medium">
                      {t("accounts.configRecording")}
                    </th>
                    <th className="py-1.5 text-right font-medium">
                      {t("accounts.column.resources")}
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {scannedRegions.map((region) => {
                    const info = a.regions.find((x) => x.region === region)
                    return (
                      <tr key={region} className="border-b last:border-b-0">
                        <td className="py-2 pr-3 font-mono text-xs">
                          {region}
                        </td>
                        <td className="py-2 pr-3">
                          {info?.reachable === false ? (
                            <span className="text-warn">
                              {t("accounts.no")}
                            </span>
                          ) : (
                            t("accounts.yes")
                          )}
                        </td>
                        <td className="py-2 pr-3">
                          {info?.configRecording === true
                            ? t("accounts.configOn")
                            : info?.configRecording === false
                              ? t("accounts.configOff")
                              : t("accounts.configUnknown")}
                        </td>
                        <td className="py-2 text-right">
                          <Link
                            to="/resources"
                            search={{
                              s: `acct:${a.accountId}`,
                              f: { awsRegion: [region] },
                            }}
                            className="text-primary hover:underline"
                          >
                            {formatNumber(
                              regionCounts.get(region) ?? 0,
                              locale,
                            )}
                          </Link>
                        </td>
                      </tr>
                    )
                  })}
                  {idleRegions.map((region) => (
                    <tr key={region} className="border-b last:border-b-0">
                      <td className="py-2 pr-3 font-mono text-xs">{region}</td>
                      <td className="py-2 pr-3">{t("accounts.yes")}</td>
                      <td className="py-2 pr-3 text-muted-foreground">
                        {t("accounts.configUnknown")}
                      </td>
                      <td className="py-2 text-right text-muted-foreground">
                        {t("accounts.notScanned")}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </QueryRegion>
        </Panel>
      </div>

      <details className="rounded-xl border bg-card px-5 py-4">
        <summary className="cursor-pointer font-semibold">
          {t("accounts.matrix")}
        </summary>
        <div className="mt-4">
          <QueryRegion
            isLoading={coverage.isLoading}
            error={coverage.error}
            onRetry={() => coverage.refetch()}
            isEmpty={!coverage.data?.length}
            emptyMessage={t("accounts.noCoverage")}
          >
            {coverage.data && (
              <div className="overflow-x-auto">
                <CoverageGrid cells={coverage.data} typeLabels={typeLabels} />
              </div>
            )}
          </QueryRegion>
        </div>
      </details>
    </>
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
