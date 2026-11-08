import { createFileRoute } from "@tanstack/react-router"

import {
  DataTable,
  tableSearchSchema,
  useTableUrlState,
} from "@/components/data-table"
import { useAccountColumns } from "@/features/columns"
import { useI18n } from "@/i18n"
import { noData as accounts } from "@/lib/no-data"

export const Route = createFileRoute("/accounts/")({
  validateSearch: tableSearchSchema,
  component: Accounts,
})

function Accounts() {
  const { t } = useI18n()
  const columns = useAccountColumns()
  const { state, setState } = useTableUrlState(Route.useSearch())
  return (
    <div className="flex h-full flex-col gap-4 px-6 py-6">
      <h1>{t("accounts.title")}</h1>
      <div className="min-h-0 flex-1">
        <DataTable<Record<string, unknown>>
          tableId="accounts"
          entity="accounts"
          columns={columns}
          rows={[]}
          total={0}
          state={state}
          onStateChange={setState}
          isLoading={accounts.isLoading}
          error={accounts.error}
          onRetry={accounts.refetch}
        />
      </div>
    </div>
  )
}
