import { createFileRoute } from "@tanstack/react-router"

import { NotAvailable } from "@/components/not-available"
import { useI18n } from "@/i18n"

export const Route = createFileRoute("/scans/$id")({
  component: ScanDetail,
})

function ScanDetail() {
  const { t } = useI18n()
  return <NotAvailable title={t("scans.detail.title")} />
}
