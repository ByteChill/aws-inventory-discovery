import {
  createFileRoute,
  Link,
  useLocation,
  useNavigate,
} from "@tanstack/react-router"
import { ChevronLeft, ChevronRight } from "lucide-react"
import { type ReactNode, useEffect, useMemo, useRef, useState } from "react"

import {
  useAccount,
  useColumns,
  useResource,
  useResourceTypes,
} from "@/api/hooks"
import {
  useAbnormalScope,
  useConfirmChange,
  useScopeCandidates,
} from "@/api/scans"
import type { ScanScopeOut } from "@/client"
import { CopyButton } from "@/components/copy-button"
import { tableSearchSchema, useTableUrlState } from "@/components/data-table"
import { EmptyState, InlineError, QueryRegion } from "@/components/inline-state"
import { Breadcrumb } from "@/components/layout/crumbs"
import { Page, PageHeader, Panel } from "@/components/layout/page"
import { Time } from "@/components/time"
import { Button } from "@/components/ui/button"
import { Dialog } from "@/components/ui/dialog"
import { ResourcePreview } from "@/features/resource-preview"
import { type MessageKey, useI18n } from "@/i18n"
import { errorText } from "@/lib/errors"
import { formatNumber } from "@/lib/format"
import { cn } from "@/lib/utils"

export const Route = createFileRoute("/scan-scopes/$scanShardId")({
  validateSearch: tableSearchSchema,
  component: ScopePage,
})

function ScopePage() {
  const { t } = useI18n()
  const { scanShardId } = Route.useParams()
  const id = Number(scanShardId)
  const scopes = useAbnormalScope(id)
  const scope = scopes.data

  if (scopes.isSuccess && scope === null)
    return (
      <Page>
        <PageHeader title={t("scanScope.title")} />
        <EmptyState
          message={t("scanScope.notFoundPage")}
          action={
            <Button asChild variant="outline">
              <Link to="/scans">{t("scanScope.backToScans")}</Link>
            </Button>
          }
        />
      </Page>
    )

  return (
    <Page>
      <QueryRegion
        isLoading={scopes.isLoading}
        error={scopes.error}
        onRetry={() => scopes.refetch()}
        isEmpty={!scope}
        emptyMessage={t("scanScope.notFoundPage")}
      >
        {scope && (
          <Detail
            key={`${scope.scanShardId}:${scope.guardDetectedAt}`}
            scope={scope}
          />
        )}
      </QueryRegion>
    </Page>
  )
}

function moreToRight(element: HTMLDivElement) {
  return element.scrollLeft + element.clientWidth < element.scrollWidth - 1
}

function Detail({ scope }: { scope: ScanScopeOut }) {
  const { t, locale } = useI18n()
  const { state, setState } = useTableUrlState(Route.useSearch())
  const navigate = useNavigate()
  const [confirming, setConfirming] = useState(false)
  const [draft, setDraft] = useState(state.q ?? "")
  const [overflowing, setOverflowing] = useState(false)
  const tableWrap = useRef<HTMLDivElement>(null)
  useEffect(() => {
    const element = tableWrap.current
    if (!element) return
    const update = () => setOverflowing(moreToRight(element))
    update()
    const observer = new ResizeObserver(update)
    observer.observe(element)
    return () => observer.disconnect()
  }, [])
  useEffect(() => {
    setDraft(state.q ?? "")
  }, [state.q])
  // Wait briefly after typing instead of rerendering the table on every key.
  useEffect(() => {
    const timer = setTimeout(() => {
      const q = draft.trim() || undefined
      if (q !== state.q) setState({ ...state, q, page: 1 })
    }, 250)
    return () => clearTimeout(timer)
  }, [draft, state, setState])
  const confirm = useConfirmChange()
  const account = useAccount(scope.accountId)
  const types = useResourceTypes()
  const typeLabel = useMemo(
    () =>
      types.data?.find((x) => x.typeName === scope.resourceType)?.label ??
      scope.resourceType,
    [types.data, scope.resourceType],
  )
  const candidates = useScopeCandidates(
    scope.scanShardId,
    state.q,
    { page: state.page, pageSize: state.pageSize },
    scope.guardDetectedAt,
  )
  const total = candidates.data?.total ?? 0
  const pages = Math.max(1, Math.ceil(total / state.pageSize))
  const accountName = account.data?.accountName ?? scope.accountId
  // Candidates carry only identity; the preview reads the full row by id.
  const [previewId, setPreviewId] = useState<number>()
  const preview = useResource(previewId)
  const columns = useColumns(scope.resourceType)
  const location = useLocation()
  const back = `${location.pathname}${location.searchStr}`

  return (
    <>
      <Breadcrumb
        items={[
          {
            key: "scans",
            content: <Link to="/scans">{t("nav.scanManagement")}</Link>,
          },
          {
            key: "anomalies",
            content: <Link to="/scans">{t("scans.anomalies")}</Link>,
          },
          { key: "review", content: t("scanScope.title") },
        ]}
      />
      <PageHeader
        title={t("scanScope.title")}
        description={t("scanScope.subtitle", {
          account: accountName,
          region: scope.region,
          type: typeLabel,
        })}
        actions={
          <Button asChild variant="outline">
            <Link to="/scans">{t("scanScope.backToScans")}</Link>
          </Button>
        }
      />

      <Panel>
        <dl className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <Field label={t("scanScope.account")}>
            {accountName}
            <span className="mt-0.5 block font-mono text-xs text-muted-foreground">
              {scope.accountId}
            </span>
          </Field>
          <Field label={t("scanScope.regionType")}>
            {scope.region}
            <span className="mt-0.5 block text-muted-foreground">
              {typeLabel}
            </span>
          </Field>
          <Field label={t("scanScope.detectedAt")}>
            <Time value={scope.guardDetectedAt} />
          </Field>
          <Field label={t("scanScope.candidates")}>
            <strong className="text-lg leading-none">
              {formatNumber(scope.candidateCount, locale)}
            </strong>
            <span className="ml-1 text-xs text-muted-foreground">
              / {t("scanScope.active")}{" "}
              {formatNumber(scope.activeCount, locale)}
            </span>
          </Field>
        </dl>
      </Panel>

      <div className="rounded-lg border border-[#e6d3a8] bg-warn-soft p-3.5 dark:border-[#5a4a1e]">
        <strong className="text-warn">{t("scanScope.noteTitle")}</strong>
        <p className="mt-1 text-muted-foreground">{t("scanScope.note")}</p>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2>
          {t("scanScope.candidatesTitle")}{" "}
          <span className="text-xs font-normal text-muted-foreground">
            {formatNumber(scope.candidateCount, locale)}
          </span>
        </h2>
        <Link
          to="/resources"
          search={{
            s: `acct:${scope.accountId}`,
            f: {
              resourceType: [scope.resourceType],
              awsRegion: [scope.region],
            },
          }}
          className="text-primary hover:underline"
        >
          {t("scanScope.viewAll")}
        </Link>
      </div>

      <search className="flex flex-wrap items-center gap-2">
        <input
          type="search"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={t("scanScope.searchPlaceholder")}
          aria-label={t("scanScope.searchPlaceholder")}
          className="h-9 w-[min(380px,100%)] rounded-md border border-input bg-card px-2.5 placeholder:text-muted-foreground"
        />
        {state.q && (
          <Button
            variant="link"
            onClick={() => {
              setDraft("")
              setState({ ...state, q: undefined, page: 1 })
            }}
          >
            {t("action.clearFilters")}
          </Button>
        )}
      </search>

      {candidates.error ? (
        <InlineError
          message={errorText(t, candidates.error)}
          onRetry={() => candidates.refetch()}
        />
      ) : !candidates.isLoading && total === 0 ? (
        <EmptyState
          message={t("scanScope.empty")}
          action={
            <Button asChild variant="outline">
              <Link
                to="/resources"
                search={{
                  s: `acct:${scope.accountId}`,
                  f: {
                    resourceType: [scope.resourceType],
                    awsRegion: [scope.region],
                  },
                }}
              >
                {t("scanScope.viewAll")}
              </Link>
            </Button>
          }
        />
      ) : (
        <>
          <div
            ref={tableWrap}
            onScroll={(event) =>
              setOverflowing(moreToRight(event.currentTarget))
            }
            className={cn(
              "overflow-auto rounded-lg border bg-card",
              overflowing &&
                "[mask-image:linear-gradient(to_right,#000_calc(100%-48px),transparent)]",
            )}
          >
            <table className="w-full min-w-[980px] table-fixed border-collapse text-left whitespace-nowrap">
              <colgroup>
                <col className="w-[280px]" />
                <col className="w-[420px]" />
                <col className="w-[140px]" />
                <col className="w-[140px]" />
              </colgroup>
              <thead>
                <tr className="border-b bg-subtle text-xs text-muted-foreground">
                  <th
                    scope="col"
                    className="sticky left-0 z-[1] bg-subtle px-3.5 py-2 font-medium"
                  >
                    {t("scanScope.column.name")}
                  </th>
                  <th scope="col" className="px-3.5 py-2 font-medium">
                    {t("scanScope.column.arn")}
                  </th>
                  <th scope="col" className="px-3.5 py-2 font-medium">
                    {t("scanScope.column.scan")}
                  </th>
                  <th scope="col" className="px-3.5 py-2 font-medium">
                    {t("scanScope.column.status")}
                  </th>
                </tr>
              </thead>
              <tbody>
                {candidates.data?.items.map((candidate) => (
                  <tr
                    key={`${candidate.resourceId}|${candidate.arn}`}
                    tabIndex={candidate.id !== null ? 0 : undefined}
                    aria-label={
                      candidate.id !== null
                        ? t("preview.row", {
                            name:
                              candidate.resourceName ?? candidate.resourceId,
                          })
                        : undefined
                    }
                    onClick={(e) => {
                      if (candidate.id === null) return
                      if ((e.target as HTMLElement).closest("a,button")) return
                      setPreviewId(candidate.id)
                    }}
                    onKeyDown={(e) => {
                      if (candidate.id === null) return
                      if (e.target !== e.currentTarget) return
                      if (e.key !== "Enter" && e.key !== " ") return
                      e.preventDefault()
                      setPreviewId(candidate.id)
                    }}
                    className={cn(
                      "border-b last:border-b-0 hover:bg-subtle",
                      candidate.id !== null &&
                        "cursor-pointer focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-primary",
                    )}
                  >
                    <td className="sticky left-0 z-[1] bg-card px-3.5 py-2.5">
                      {candidate.id !== null ? (
                        <Link
                          to="/resources/$id"
                          params={{ id: String(candidate.id) }}
                          search={{ back }}
                          className="font-semibold text-primary hover:underline"
                        >
                          {candidate.resourceName ?? candidate.resourceId}
                        </Link>
                      ) : (
                        <span className="text-muted-foreground">
                          {t("scanScope.purged")}
                        </span>
                      )}
                      <span
                        className="mt-0.5 block max-w-[28rem] truncate font-mono text-xs text-muted-foreground"
                        title={candidate.resourceId}
                      >
                        {candidate.resourceId}
                      </span>
                    </td>
                    <td className="px-3.5 py-2.5">
                      <span className="flex min-w-0 items-center gap-1 font-mono text-xs text-muted-foreground">
                        <span
                          className="max-w-[28rem] truncate"
                          title={candidate.arn}
                        >
                          {candidate.arn}
                        </span>
                        <CopyButton value={candidate.arn} />
                      </span>
                    </td>
                    <td className="px-3.5 py-2.5">
                      <span className="text-warn">
                        {t("scanScope.notFound")}
                      </span>
                      <span className="mt-0.5 block text-xs text-muted-foreground">
                        <Time value={scope.guardDetectedAt} />
                      </span>
                    </td>
                    <td className="px-3.5 py-2.5">
                      {t("scanScope.keptStatus")}
                      <span className="mt-0.5 block text-xs text-muted-foreground">
                        {candidate.inventoryStatus === "purged"
                          ? t("scanScope.purged")
                          : t(
                              `status.${candidate.inventoryStatus}` as MessageKey,
                            )}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex flex-wrap items-center justify-between gap-3 text-xs text-muted-foreground">
            <span>
              {t("table.range.candidates", {
                from: total === 0 ? 0 : (state.page - 1) * state.pageSize + 1,
                to: Math.min(state.page * state.pageSize, total),
                count: total,
              })}
            </span>
            <div className="flex items-center gap-2">
              <select
                value={state.pageSize}
                onChange={(e) =>
                  setState({
                    ...state,
                    pageSize: Number(e.target.value),
                    page: 1,
                  })
                }
                aria-label={t("table.pageSize")}
                className="h-8 rounded-md border border-input bg-card px-2"
              >
                {[10, 25, 50].map((n) => (
                  <option key={n} value={n}>
                    {t("table.pageSizeOption", { count: n })}
                  </option>
                ))}
              </select>
              <span>{t("table.page", { page: state.page, pages })}</span>
              <Button
                variant="outline"
                size="icon"
                aria-label={t("table.prev")}
                disabled={state.page <= 1}
                onClick={() => setState({ ...state, page: state.page - 1 })}
              >
                <ChevronLeft />
              </Button>
              <Button
                variant="outline"
                size="icon"
                aria-label={t("table.next")}
                disabled={state.page >= pages}
                onClick={() => setState({ ...state, page: state.page + 1 })}
              >
                <ChevronRight />
              </Button>
            </div>
          </div>
        </>
      )}

      <Panel title={t("scanScope.decisionTitle")}>
        <div className="flex flex-col gap-3">
          <p className="max-w-[780px] text-muted-foreground">
            {t("scanScope.decisionBody", { count: scope.candidateCount })}
          </p>
          <div className="flex flex-wrap gap-2">
            <Button asChild variant="outline">
              <Link to="/scans">{t("scanScope.hold")}</Link>
            </Button>
            <Button onClick={() => setConfirming(true)}>
              {t("action.confirmChange")}
            </Button>
          </div>
        </div>
      </Panel>

      <Dialog
        open={confirming}
        onClose={() => setConfirming(false)}
        title={t("action.confirmChange")}
      >
        <p>
          {t("scanScope.confirmScope" as MessageKey, {
            count: scope.candidateCount,
            account: accountName,
            region: scope.region,
            type: typeLabel,
            candidates: formatNumber(scope.candidateCount, locale),
            active: formatNumber(scope.activeCount, locale),
          })}
        </p>
        <p>{t("scanScope.confirmNote")}</p>
        {confirm.error && <InlineError message={errorText(t, confirm.error)} />}
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={() => setConfirming(false)}>
            {t("action.cancel")}
          </Button>
          <Button
            disabled={confirm.isPending}
            onClick={() =>
              confirm.mutate(
                {
                  accountId: scope.accountId,
                  awsRegion: scope.region,
                  resourceType: scope.resourceType,
                },
                {
                  onSuccess: (scan) => {
                    setConfirming(false)
                    navigate({
                      to: "/scans/$id",
                      params: { id: String(scan.scanRequestId) },
                    })
                  },
                },
              )
            }
          >
            {t("action.confirm")}
          </Button>
        </div>
      </Dialog>

      <ResourcePreview
        resource={previewId !== undefined ? (preview.data ?? null) : null}
        typeColumns={(columns.data ?? []).filter((c) => c.scope === "type")}
        typeLabel={typeLabel}
        back={back}
        onClose={() => setPreviewId(undefined)}
      />
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
