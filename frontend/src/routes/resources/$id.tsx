import { createFileRoute, Link } from "@tanstack/react-router"
import { ArrowLeft, Loader2 } from "lucide-react"
import { type ReactNode, useMemo } from "react"
import { z } from "zod"
import {
  useColumns,
  useLookup,
  useRelationships,
  useResource,
  useResourceTypes,
} from "@/api/hooks"
import { useRefreshResource } from "@/api/scans"
import type { RelationshipEntry, ResourceDetail } from "@/client"
import { CopyButton } from "@/components/copy-button"
import { InlineError, QueryRegion } from "@/components/inline-state"
import { JsonViewer } from "@/components/json-viewer"
import { Breadcrumb } from "@/components/layout/crumbs"
import { Page, PageHeader, Panel, Section } from "@/components/layout/page"
import { Time, TimeSentence } from "@/components/time"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { NAV_GROUPS } from "@/features/inventory-nav"
import { refreshReason } from "@/features/scan-parts"
import { SERVICE_ICON } from "@/features/service-icons"
import { type MessageKey, useI18n } from "@/i18n"
import { basePath } from "@/lib/base-path"
import { errorText } from "@/lib/errors"
import { scopeParam } from "@/lib/scope"
import { cn } from "@/lib/utils"

// `id` is the numeric resource id, or a URL-encoded ARN from the search box;
// an ARN resolves through GET /resources/lookup, which returns several rows for
// a shared AMI.
export const Route = createFileRoute("/resources/$id")({
  validateSearch: z.object({
    s: scopeParam,
    /** Where the resource was opened from, for the back link. */
    back: z.string().optional(),
  }),
  component: ResourcePage,
})

function ResourcePage() {
  const { t } = useI18n()
  const { id } = Route.useParams()
  const numeric = /^\d+$/.test(id)
  const byId = useResource(numeric ? Number(id) : undefined)
  const byArn = useLookup(numeric ? undefined : id)
  const query = numeric ? byId : byArn
  const rows: ResourceDetail[] = numeric
    ? byId.data
      ? [byId.data]
      : []
    : (byArn.data ?? [])

  if (rows.length > 1) return <AccountChooser rows={rows} />

  return (
    <Page>
      <QueryRegion
        isLoading={query.isLoading}
        error={query.error}
        onRetry={() => query.refetch()}
        isEmpty={rows.length === 0}
        emptyMessage={t("error.resource_not_found")}
      >
        {rows[0] && <Detail resource={rows[0]} />}
      </QueryRegion>
    </Page>
  )
}

function AccountChooser({ rows }: { rows: ResourceDetail[] }) {
  const { t } = useI18n()
  return (
    <Page>
      <PageHeader title={rows[0].arn} mono />
      <Section title={t("detail.pickAccount")}>
        <ul className="flex flex-col gap-1">
          {rows.map((r) => (
            <li key={r.id}>
              <Link
                to="/resources/$id"
                params={{ id: String(r.id) }}
                className="text-primary hover:underline"
              >
                {r.account.accountName}
              </Link>{" "}
              <span className="font-mono text-muted-foreground">
                {r.accountId}
              </span>
            </li>
          ))}
        </ul>
      </Section>
    </Page>
  )
}

function Detail({ resource: r }: { resource: ResourceDetail }) {
  const { t } = useI18n()
  const { back } = Route.useSearch()
  const types = useResourceTypes()
  const typeColumns = useColumns(r.resourceType)
  const refresh = useRefreshResource()
  const typeLabel =
    types.data?.find((x) => x.typeName === r.resourceType)?.label ??
    r.resourceType
  const service = useMemo(
    () =>
      NAV_GROUPS.flatMap((g) => g.services).find((x) =>
        x.types.includes(r.resourceType),
      ),
    [r.resourceType],
  )
  const group = useMemo(
    () =>
      service
        ? NAV_GROUPS.find((g) => g.services.includes(service))
        : undefined,
    [service],
  )
  const Icon = service ? SERVICE_ICON[service.key] : undefined
  const backLink = backInfo(back, t)
  const columns = (typeColumns.data ?? []).filter((c) => c.scope === "type")
  const region = r.awsRegion === "global" ? t("region.global") : r.awsRegion

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
          ...(group
            ? [
                {
                  key: "group",
                  content: (
                    <Link
                      to="/resources"
                      search={{
                        f: {
                          resourceType: group.services.flatMap((s) => s.types),
                        },
                      }}
                    >
                      {t(`nav.group.${group.key}`)}
                    </Link>
                  ),
                },
              ]
            : []),
          ...(service
            ? [
                {
                  key: "service",
                  content: (
                    <Link
                      to="/resources"
                      search={{ f: { resourceType: service.types } }}
                    >
                      {service.label}
                    </Link>
                  ),
                },
              ]
            : []),
          {
            key: "type",
            content: (
              <Link
                to="/resources"
                search={{ f: { resourceType: [r.resourceType] } }}
              >
                {typeLabel}
              </Link>
            ),
          },
          { key: "resource", content: r.resourceName ?? r.resourceId },
        ]}
      />

      <PageHeader
        title={
          <>
            {Icon && (
              <span className="inline-grid size-9 shrink-0 place-items-center rounded-lg bg-primary-soft text-primary">
                <Icon className="size-5" />
              </span>
            )}
            {r.resourceName ?? r.resourceId}
          </>
        }
        description={`${typeLabel}，${r.account.accountName}，${region}`}
        actions={
          <>
            <Button variant="outline" asChild>
              <a href={`${basePath}${backLink.href}`}>
                <ArrowLeft />
                {backLink.label}
              </a>
            </Button>
            <Button
              variant="outline"
              disabled={refresh.phase !== "idle"}
              onClick={() =>
                refresh.run({
                  accountId: r.accountId,
                  awsRegion: r.awsRegion,
                  resourceType: r.resourceType,
                  resourceId: r.resourceId,
                })
              }
            >
              {refresh.phase === "waiting" && (
                <Loader2 className="animate-spin" />
              )}
              {refresh.phase === "queued"
                ? t("detail.refreshing")
                : t("action.refresh")}
            </Button>
            <CopyButton value={r.arn} text={t("action.copyArn")} />
          </>
        }
      />
      {refresh.error != null && (
        <p role="alert" className="text-danger">
          {t("detail.refreshFailed", {
            reason: refreshReason(t, refresh.error),
          })}
        </p>
      )}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-sm text-muted-foreground">
        {r.status !== "active" && (
          <Badge tone={r.status === "missing" ? "warn" : "neutral"}>
            {t(`status.${r.status}`)}
          </Badge>
        )}
        {r.stale && <Badge>{t("status.stale")}</Badge>}
        <span>
          <TimeSentence
            message="time.updatedAt"
            value={r.observedAt}
            note={t(`source.${r.observedSource}`)}
          />
        </span>
      </div>

      <Panel>
        <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 xl:grid-cols-5">
          <SummaryField label={t("detail.id")}>
            <span className="font-mono break-all">{r.resourceId}</span>
          </SummaryField>
          <SummaryField label={t("detail.account")}>
            <Link
              to="/accounts/$accountId"
              params={{ accountId: r.accountId }}
              title={r.account.accountName}
              className="block truncate text-primary hover:underline"
            >
              {r.account.accountName}
            </Link>
            <span className="mt-0.5 flex min-w-0 items-center gap-0.5 font-mono text-xs text-muted-foreground">
              <span className="truncate">{r.accountId}</span>
              <CopyButton value={r.accountId} className="size-5 shrink-0" />
            </span>
          </SummaryField>
          <SummaryField label={t("detail.env")}>
            {t(`env.${r.account.accountType}`)}
          </SummaryField>
          <SummaryField label={t("detail.region")}>
            <span className="font-mono">{region}</span>
          </SummaryField>
          <SummaryField label={t("detail.eim")}>
            {r.account.eimId ? (
              <>
                <Link
                  to="/"
                  search={{ s: `app:${r.account.eimId}` }}
                  title={r.account.eimApp ?? r.account.eimId}
                  className="block truncate text-primary hover:underline"
                >
                  {r.account.eimApp ?? r.account.eimId}
                </Link>
                <span className="mt-0.5 flex min-w-0 items-center gap-0.5 font-mono text-xs text-muted-foreground">
                  <span className="truncate">{r.account.eimId}</span>
                  <CopyButton
                    value={r.account.eimId}
                    className="size-5 shrink-0"
                  />
                </span>
              </>
            ) : (
              <span className="text-muted-foreground">{t("common.none")}</span>
            )}
          </SummaryField>
        </dl>
      </Panel>

      <div className="grid items-start gap-4 lg:grid-cols-2">
        <div className="flex flex-col gap-4">
          <Panel title={t("detail.config")}>
            {columns.length === 0 ? (
              <p className="text-muted-foreground">{t("common.none")}</p>
            ) : (
              <dl>
                {columns.map((column) => (
                  <Field key={column.key} label={column.label}>
                    <span className="break-words">
                      {displayValue(r.attrs[column.key], t)}
                    </span>
                  </Field>
                ))}
              </dl>
            )}
          </Panel>
          <Panel title={t("detail.relationships")}>
            <Relationships id={r.id} />
          </Panel>
        </div>

        <div className="flex flex-col gap-4">
          <Panel title={t("detail.identifiers")}>
            <dl>
              <Field label={t("detail.arn")}>
                <span className="flex items-start gap-0.5">
                  <span className="font-mono break-all">{r.arn}</span>
                  <CopyButton value={r.arn} />
                </span>
              </Field>
              <Field label={t("detail.type")}>
                <span className="font-mono">{r.resourceType}</span>
              </Field>
              {r.account.baId && (
                <Field label="BA">
                  <span className="font-mono">{r.account.baId}</span>
                </Field>
              )}
              <Field label={t("detail.firstSeen")}>
                <Time value={r.firstSeenAt} />
              </Field>
              <Field label={t("detail.lastChanged")}>
                <Time value={r.lastChangedAt} />
              </Field>
              {r.missingSince && (
                <Field label={t("detail.missingSince")}>
                  <Time value={r.missingSince} />
                </Field>
              )}
              {r.deletedAt && (
                <Field label={t("detail.deletedAt")}>
                  <Time value={r.deletedAt} />
                </Field>
              )}
            </dl>
          </Panel>

          <Panel title={t("detail.tags")}>
            {r.tags.length === 0 ? (
              <p className="text-muted-foreground">{t("detail.noTags")}</p>
            ) : (
              <dl>
                {r.tags.map((tag) => (
                  <Field key={tag.key} label={tag.key}>
                    <span className="break-all">{tag.value}</span>
                  </Field>
                ))}
              </dl>
            )}
          </Panel>
        </div>
      </div>

      {r.configuration && (
        <Panel title={t("detail.configuration")}>
          <JsonViewer value={r.configuration} />
        </Panel>
      )}

      {r.live && (
        <Panel
          title={t("detail.live")}
          actions={
            <span className="text-xs text-muted-foreground">
              <TimeSentence
                message="time.observedAt"
                value={r.live.observedAt}
              />
            </span>
          }
        >
          <JsonViewer
            value={r.live.properties}
            searchLabel={t("detail.liveSearch")}
          />
        </Panel>
      )}
    </>
  )
}

function SummaryField({
  label,
  children,
}: {
  label: ReactNode
  children: ReactNode
}) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="mt-0.5 break-words">{children}</dd>
    </div>
  )
}

function displayValue(
  value: unknown,
  t: ReturnType<typeof useI18n>["t"],
): string {
  if (value === null || value === undefined || value === "")
    return t("common.none")
  if (typeof value === "boolean")
    return value ? t("common.yes") : t("common.no")
  if (Array.isArray(value)) return value.map(String).join(t("common.separator"))
  if (typeof value === "object") return JSON.stringify(value)
  return String(value)
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0">{children}</dd>
    </>
  )
}

type Group = {
  key: string
  direction: "out" | "in"
  name: string
  items: RelationshipEntry[]
}

function Relationships({ id }: { id: number }) {
  const { t } = useI18n()
  const query = useRelationships(id)
  const types = useResourceTypes()
  const labels = useMemo(
    () => new Map(types.data?.map((x) => [x.typeName, x.label])),
    [types.data],
  )

  const groups = useMemo(() => {
    const byKey = new Map<string, Group>()
    for (const page of query.data?.pages ?? []) {
      for (const entry of page.items) {
        const key = `${entry.direction}|${entry.relationName}`
        const group = byKey.get(key) ?? {
          key,
          direction: entry.direction,
          name: entry.relationName,
          items: [],
        }
        group.items.push(entry)
        byKey.set(key, group)
      }
    }
    return [...byKey.values()].sort((a, b) =>
      a.direction === b.direction
        ? a.name.localeCompare(b.name)
        : a.direction === "out"
          ? -1
          : 1,
    )
  }, [query.data])

  return (
    <QueryRegion
      isLoading={query.isLoading}
      error={query.error}
      onRetry={() => query.refetch()}
      isEmpty={groups.length === 0}
      emptyMessage={t("detail.noRelationships")}
    >
      <div className="flex flex-col gap-4">
        {groups.map((g) => (
          <div key={g.key} className="flex flex-col gap-1">
            <h3>
              {g.name}
              <span
                className={cn(
                  "font-normal text-muted-foreground",
                  g.name && "ml-2",
                )}
              >
                {t(`detail.rel.${g.direction}` as MessageKey)}
              </span>
            </h3>
            <ul className="flex flex-col">
              {g.items.map((entry) => (
                <li
                  key={`${entry.resourceType}|${entry.resourceKey}`}
                  className="flex flex-wrap gap-x-3"
                >
                  <span className="text-muted-foreground">
                    {labels.get(entry.resourceType) ?? entry.resourceType}
                  </span>
                  {entry.resource ? (
                    <Link
                      to="/resources/$id"
                      params={{ id: String(entry.resource.id) }}
                      className="text-primary hover:underline"
                    >
                      {entry.resource.resourceName ?? entry.resource.resourceId}
                    </Link>
                  ) : (
                    <span
                      title={t("detail.notInInventory")}
                      className="font-mono text-muted-foreground break-all"
                    >
                      {entry.resourceKey}
                    </span>
                  )}
                </li>
              ))}
            </ul>
          </div>
        ))}
        {query.isFetchNextPageError && (
          <InlineError
            message={errorText(t, query.error)}
            onRetry={() => query.fetchNextPage()}
          />
        )}
        {query.hasNextPage && (
          <div>
            <Button
              variant="outline"
              disabled={query.isFetchingNextPage}
              onClick={() => query.fetchNextPage()}
            >
              {t("action.loadMore")}
            </Button>
          </div>
        )}
      </div>
    </QueryRegion>
  )
}

/** The back link keeps its source: the list with its filters, or the scan, task or review page. */
function backInfo(
  back: string | undefined,
  t: ReturnType<typeof useI18n>["t"],
) {
  const href = back ?? "/resources"
  const scan = href.match(/^\/scans\/(\d+)/)
  if (scan) {
    return {
      href,
      label: t("resources.backToScan", { id: Number(scan[1]) }),
    }
  }
  const task = href.match(/^\/tasks\/(\d+)/)
  if (task) {
    return { href, label: t("detail.backToTask", { id: Number(task[1]) }) }
  }
  if (href.startsWith("/scan-scopes/")) {
    return { href, label: t("detail.backToReview") }
  }
  return { href, label: t("detail.backToList") }
}
