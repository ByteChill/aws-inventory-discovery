import { createFileRoute, Link, useNavigate } from "@tanstack/react-router"
import { Plus } from "lucide-react"
import { useState } from "react"

import { useResourceTypes } from "@/api/hooks"
import { useReports, useReportsOverview } from "@/api/reports"
import type { ReportInfo } from "@/client"
import { Page, PageHeader } from "@/components/layout/page"
import { Time } from "@/components/time"
import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { ReportEditor } from "@/features/report-editor"
import { useI18n } from "@/i18n"
import { errorText } from "@/lib/errors"
import { formatNumber } from "@/lib/format"

export const Route = createFileRoute("/reports/")({
  validateSearch: (search: Record<string, unknown>) => ({
    s: typeof search.s === "string" ? search.s : undefined,
  }),
  component: Reports,
})

function Reports() {
  const { t } = useI18n()
  const navigate = useNavigate()
  const search = Route.useSearch()
  const reports = useReports()
  const overview = useReportsOverview(search.s)
  const types = useResourceTypes()
  const [creating, setCreating] = useState(false)

  const scopeValue = search.s
  const counts = new Map(
    (overview.data?.items ?? []).map((item) => [item.reportId, item]),
  )
  const typeLabel = (typeName: string | null) =>
    typeName
      ? (types.data?.find((type) => type.typeName === typeName)?.label ??
        typeName)
      : t("reports.allTypes")

  return (
    <Page>
      <PageHeader
        title={t("reports.title")}
        description={t("reports.subtitle")}
        actions={
          <Button onClick={() => setCreating(true)}>
            <Plus />
            {t("reports.create")}
          </Button>
        }
      />

      {reports.isLoading ? (
        <Skeleton className="h-40 w-full" />
      ) : reports.error ? (
        <p role="alert" className="text-sm text-danger">
          {errorText(t, reports.error)}
        </p>
      ) : (reports.data ?? []).length === 0 ? (
        <div className="rounded-xl border bg-card px-6 py-10 text-center">
          <p className="font-medium">{t("reports.empty")}</p>
          <p className="mt-1 text-sm text-muted-foreground">
            {t("reports.emptyHint")}
          </p>
        </div>
      ) : (
        <ul className="flex flex-col gap-2">
          {(reports.data ?? []).map((report) => (
            <ReportRow
              key={report.id}
              report={report}
              count={counts.get(report.id)}
              typeLabel={typeLabel(report.subjectType)}
              s={scopeValue}
            />
          ))}
        </ul>
      )}

      {creating && (
        <ReportEditor
          types={types.data ?? []}
          onClose={() => setCreating(false)}
          onSaved={(saved) => {
            setCreating(false)
            void navigate({
              to: "/reports/$id",
              params: { id: String(saved.id) },
              search: search.s ? { s: search.s } : {},
            })
          }}
        />
      )}
    </Page>
  )
}

function ReportRow({
  report,
  count,
  typeLabel,
  s,
}: {
  report: ReportInfo
  count: { count: number; error?: string | null } | undefined
  typeLabel: string
  s: string | undefined
}) {
  const { t, locale } = useI18n()
  return (
    <li>
      <Link
        to="/reports/$id"
        params={{ id: String(report.id) }}
        search={{ s }}
        className="flex min-h-16 items-center gap-4 rounded-xl border bg-card px-5 py-3 hover:bg-subtle"
      >
        <span className="min-w-0 flex-1">
          <span className="flex items-center gap-2 font-medium">
            <span className="truncate">{report.name}</span>
            {!report.enabled && <Badge>{t("reports.disabled")}</Badge>}
          </span>
          <span className="mt-0.5 block truncate text-sm text-muted-foreground">
            {report.description || typeLabel}
          </span>
        </span>
        <span className="shrink-0 text-sm text-muted-foreground">
          {typeLabel}
        </span>
        <span className="w-28 shrink-0 text-right tabular-nums">
          {count?.error ? (
            <Badge tone="warn">{t("reports.countError")}</Badge>
          ) : count ? (
            formatNumber(count.count, locale)
          ) : (
            "—"
          )}
        </span>
        <span className="w-40 shrink-0 text-right text-sm text-muted-foreground max-[800px]:hidden">
          <Time value={report.updatedAt} />
        </span>
      </Link>
    </li>
  )
}
