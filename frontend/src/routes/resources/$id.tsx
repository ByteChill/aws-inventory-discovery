import { createFileRoute, Link } from "@tanstack/react-router"
import { Loader2 } from "lucide-react"
import { type ReactNode, useMemo } from "react"
import { z } from "zod"
import {
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
import { Page, PageHeader, Section } from "@/components/layout/page"
import { Time, TimeSentence } from "@/components/time"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { refreshReason } from "@/features/scan-parts"
import { type MessageKey, useI18n } from "@/i18n"
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
  const types = useResourceTypes()
  const refresh = useRefreshResource()
  const typeLabel =
    types.data?.find((x) => x.typeName === r.resourceType)?.label ??
    r.resourceType

  return (
    <>
      <div className="flex flex-col gap-2">
        <PageHeader
          title={r.resourceName ?? r.resourceId}
          mono={!r.resourceName}
          actions={
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
          }
        />
        {refresh.error != null && (
          <p role="alert" className="text-danger">
            {t("detail.refreshFailed", {
              reason: refreshReason(t, refresh.error),
            })}
          </p>
        )}
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <span>{typeLabel}</span>
          {r.status !== "active" && (
            <Badge tone={r.status === "missing" ? "warn" : "neutral"}>
              {t(`status.${r.status}`)}
            </Badge>
          )}
          {r.stale && <Badge>{t("status.stale")}</Badge>}
          <span className="text-muted-foreground">
            <TimeSentence
              message="time.updatedAt"
              value={r.observedAt}
              note={t(`source.${r.observedSource}`)}
            />
          </span>
        </div>
      </div>

      <Section title={t("detail.basic")}>
        <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1.5">
          <Field label={t("detail.arn")}>
            <span className="inline-flex items-start gap-0.5">
              <span className="font-mono break-all">{r.arn}</span>
              <CopyButton value={r.arn} />
            </span>
          </Field>
          <Field label={t("detail.type")}>
            <span className="font-mono">{r.resourceType}</span>
          </Field>
          <Field label={t("detail.account")}>
            <Link
              to="/accounts/$accountId"
              params={{ accountId: r.accountId }}
              title={r.accountId}
              className="text-primary hover:underline"
            >
              {r.account.accountName}
            </Link>
          </Field>
          {r.account.eimId && (
            <Field label={t("detail.eim")}>
              <span className="font-mono">{r.account.eimId}</span>
              {r.account.eimApp && ` ${r.account.eimApp}`}
            </Field>
          )}
          {r.account.baId && (
            <Field label={t("detail.ba")}>
              <span className="font-mono">{r.account.baId}</span>
            </Field>
          )}
          <Field label={t("detail.env")}>
            {t(`env.${r.account.accountType}`)}
          </Field>
          <Field label={t("detail.region")}>
            <span className="font-mono">{r.awsRegion}</span>
          </Field>
          <Field label={t("detail.firstSeen")}>
            <Time value={r.firstSeenAt} />
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
      </Section>

      <Section title={t("detail.tags")}>
        {r.tags.length === 0 ? (
          <p className="text-muted-foreground">{t("detail.noTags")}</p>
        ) : (
          <table className="w-full max-w-3xl border-separate border-spacing-0 text-left">
            <thead>
              <tr>
                <th className="border-b py-1.5 pr-4 font-medium">
                  {t("detail.tagKey")}
                </th>
                <th className="border-b py-1.5 font-medium">
                  {t("detail.tagValue")}
                </th>
              </tr>
            </thead>
            <tbody>
              {r.tags.map((tag) => (
                <tr key={tag.key}>
                  <td className="border-b py-1.5 pr-4 align-top break-all">
                    {tag.key}
                  </td>
                  <td className="border-b py-1.5 align-top break-all">
                    {tag.value}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Section>

      <Section title={t("detail.relationships")}>
        <Relationships id={r.id} />
      </Section>

      {r.configuration && (
        <Section title={t("detail.configuration")}>
          <JsonViewer value={r.configuration} />
        </Section>
      )}

      {r.live && (
        <Section
          title={t("detail.live")}
          actions={
            <span className="text-muted-foreground">
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
        </Section>
      )}
    </>
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
