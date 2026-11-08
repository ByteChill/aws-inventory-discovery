import { createFileRoute } from "@tanstack/react-router"

import { NotAvailable } from "@/components/not-available"
import { useI18n } from "@/i18n"

export const Route = createFileRoute("/scans/")({
  component: Scans,
})

// Scans, the scan form and tasks need the write and scan endpoints (next milestone).
function Scans() {
  const { t } = useI18n()
  return <NotAvailable title={t("scans.title")} />
}
