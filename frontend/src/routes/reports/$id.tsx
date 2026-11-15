import { createFileRoute, Link, useNavigate } from "@tanstack/react-router"
import { Download, Pencil, Trash2 } from "lucide-react"
import { useMemo, useState } from "react"

import { useColumns, useResourceTypes } from "@/api/hooks"
import { useDeleteReport, useReport, useReportResults } from "@/api/reports"
import type { ReportRow } from "@/client"
import {
  type Column,
  DataTable,
  type RowFlag,
  tableSearchSchema,
  useTableUrlState,
} from "@/components/data-table"
import { InlineError } from "@/components/inline-state"
import { Breadcrumb } from "@/components/layout/crumbs"
import { Page, PageHeader } from "@/components/layout/page"
import { Button } from "@/components/ui/button"
import { Dialog } from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { ReportEditor } from "@/features/report-editor"
import { useI18n } from "@/i18n"
import { basePath } from "@/lib/base-path"
import { errorText } from "@/lib/errors"

export const Route = createFileRoute("/reports/$id")({
  validateSearch: tableSearchSchema,
  component: ReportDetailPage,
})

/** Columns every result shows so a row can be identified, whatever the report selected. */
const IDENTITY_KEYS = ["resourceName", "accountId", "awsRegion"]

function ReportDetailPage() {
  const { t, locale } = useI18n()
  const navigate = useNavigate()
  const { id } = Route.useParams()
  const reportId = Number(id)
  const search = Route.useSearch()
  const { state, setState } = useTableUrlState(search)
  const report = useReport(Number.isNaN(reportId) ? undefined : reportId)
  const types = useResourceTypes()
  const common = useColumns(undefined)
  const [editing, setEditing] = useState(false)
  const [confirming, setConfirming] = useState(false)
  const remove = useDeleteReport()
  const results = useReportResults(reportId, state, search.s)

  const columns = useMemo(() => {
    const reportColumns: Column[] = (results.data?.columns ?? []).map(
      (column) => ({
        key: column.key,
        label: column.label,
        type: (column.type === "tags" ? "text" : column.type) as Column["type"],
        mono: !!column.mono,
        sortable: !column.array,
        filterable: false,
        scope: "type",
      }),
    )
    const known = new Set(reportColumns.map((column) => column.key))
    const identity: Column[] = (common.data ?? [])
      .filter((column) => IDENTITY_KEYS.includes(column.key))
      .filter((column) => !known.has(column.key))
      .map((column) => ({ ...column, sortable: false, filterable: false }))
    return [...identity, ...reportColumns]
  }, [results.data?.columns, common.data])

  const typeLabel = report.data?.subjectType
    ? (types.data?.find((type) => type.typeName === report.data?.subjectType)
        ?.label ?? report.data.subjectType)
    : t("reports.allTypes")

  const exportHref = (format: "csv" | "jsonl") => {
    const params = new URLSearchParams({ format, lang: locale })
    if (state.sort) params.set("sort", state.sort)
    if (search.s) params.set("s", search.s)
    return `${basePath}/api/v1/reports/${reportId}/export?${params}`
  }

  const cellValue = (row: ReportRow, key: string) => {
    const record = row as unknown as Record<string, unknown>
    if (key in (row.values ?? {})) return row.values[key]
    if (key in record) return record[key]
    const account = row.account as unknown as Record<string, unknown>
    return account[key]
  }

  function onDelete() {
    remove.mutate(reportId, {
      onSuccess: () => {
        setConfirming(false)
        void navigate({
          to: "/reports",
          search: { s: search.s },
        })
      },
    })
  }

  return (
    <Page className="min-h-0 gap-4">
      <div className="flex flex-col gap-3">
        <Breadcrumb
          items={[
            {
              key: "reports",
              content: (
                <Link to="/reports" search={{ s: search.s }}>
                  {t("reports.title")}
                </Link>
              ),
            },
            { key: "page", content: report.data?.name ?? t("reports.report") },
          ]}
        />
        <PageHeader
          title={report.data?.name ?? t("reports.report")}
          description={
            report.data
              ? report.data.description ||
                t("reports.detailSubtitle", { type: typeLabel })
              : undefined
          }
          actions={
            report.data ? (
              <>
                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button variant="outline">
                      <Download />
                      {t("action.export")}
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent className="w-64">
                    <p className="px-3 py-1.5 text-xs text-muted-foreground">
                      {t("reports.exportTip", {
                        count: results.data?.total ?? 0,
                      })}
                    </p>
                    <DropdownMenuItem asChild>
                      <a href={exportHref("csv")}>{t("action.exportCsv")}</a>
                    </DropdownMenuItem>
                    <DropdownMenuItem asChild>
                      <a href={exportHref("jsonl")}>
                        {t("action.exportJsonl")}
                      </a>
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
                <Button variant="outline" onClick={() => setEditing(true)}>
                  <Pencil />
                  {t("reports.edit")}
                </Button>
                <Button
                  variant="destructive"
                  onClick={() => setConfirming(true)}
                >
                  <Trash2 />
                  {t("reports.delete")}
                </Button>
              </>
            ) : (
              // A stored rule that no longer validates can still be removed.
              <Button
                variant="destructive"
                onClick={() => setConfirming(true)}
                disabled={Number.isNaN(reportId)}
              >
                <Trash2 />
                {t("reports.delete")}
              </Button>
            )
          }
        />
      </div>

      {report.error ? (
        <InlineError message={errorText(t, report.error)} />
      ) : (
        <div className="min-h-0 flex-1">
          <DataTable<ReportRow>
            tableId="report-results"
            entity="resources"
            columns={columns}
            rows={results.data?.items ?? []}
            total={results.data?.total ?? 0}
            state={state}
            onStateChange={setState}
            isLoading={report.isLoading || results.isLoading}
            error={results.error}
            onRetry={() => void results.refetch()}
            getRowId={(row) => String(row.id)}
            getCellValue={cellValue}
            getRowFlags={(row) => {
              const flags: RowFlag[] = []
              if (row.status !== "active") flags.push(row.status)
              if (row.stale) flags.push("stale")
              return flags
            }}
            renderCell={(column, row) => {
              if (column.key === "resourceName") {
                return (
                  <Link
                    to="/resources/$id"
                    params={{ id: String(row.id) }}
                    title={row.resourceName ?? row.resourceId}
                    className="block truncate font-semibold text-primary hover:underline"
                  >
                    {row.resourceName ?? row.resourceId}
                  </Link>
                )
              }
              const value = row.values?.[column.key]
              if (Array.isArray(value)) {
                return (
                  <span
                    className="block max-w-96 truncate"
                    title={value.join(", ")}
                  >
                    {value.join(", ")}
                  </span>
                )
              }
              return undefined
            }}
            columnMenu={false}
            filterPopovers={false}
            stickyFirst
            toolbar={
              <p className="text-sm text-muted-foreground">
                {t("reports.detailRule", { type: typeLabel })}
              </p>
            }
          />
        </div>
      )}

      {editing && report.data && (
        <ReportEditor
          initial={report.data}
          types={types.data ?? []}
          onClose={() => setEditing(false)}
          onSaved={() => {
            setEditing(false)
            void results.refetch()
          }}
        />
      )}

      {confirming && (
        <Dialog
          open
          onClose={() => setConfirming(false)}
          title={t("reports.deleteTitle", {
            name: report.data?.name ?? t("reports.report"),
          })}
        >
          <p className="text-sm text-muted-foreground">
            {t("reports.deleteBody")}
          </p>
          {remove.error && (
            <p role="alert" className="text-sm text-danger">
              {errorText(t, remove.error)}
            </p>
          )}
          <div className="flex justify-end gap-2">
            <Button variant="outline" onClick={() => setConfirming(false)}>
              {t("action.cancel")}
            </Button>
            <Button
              variant="destructive"
              disabled={remove.isPending}
              onClick={onDelete}
            >
              {t("reports.delete")}
            </Button>
          </div>
        </Dialog>
      )}
    </Page>
  )
}
