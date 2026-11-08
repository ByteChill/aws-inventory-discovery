import { createFileRoute } from "@tanstack/react-router"

import {
  DataTable,
  tableSearchSchema,
  useTableUrlState,
} from "@/components/data-table"
import { useScanColumns } from "@/features/columns"
import { useI18n } from "@/i18n"
import { noData as scans } from "@/lib/no-data"

export const Route = createFileRoute("/scans/")({
  validateSearch: tableSearchSchema,
  component: Scans,
})

// The current-status blocks (progress, backlog, failures, possibly deleted,
// anomalies) render only when GET /scans/summary reports a problem, so with
// no data there is nothing above the history.
function Scans() {
  const { t } = useI18n()
  const columns = useScanColumns()
  const { state, setState } = useTableUrlState(Route.useSearch())
  return (
    <div className="flex h-full flex-col gap-4 px-6 py-6">
      <h1>{t("scans.title")}</h1>
      <h2>{t("scans.history")}</h2>
      <div className="min-h-0 flex-1">
        <DataTable<Record<string, unknown>>
          tableId="scans"
          entity="scans"
          columns={columns}
          rows={[]}
          total={0}
          state={state}
          onStateChange={setState}
          isLoading={scans.isLoading}
          error={scans.error}
          onRetry={scans.refetch}
        />
      </div>
    </div>
  )
}
