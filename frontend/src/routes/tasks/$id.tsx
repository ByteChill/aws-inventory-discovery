import { createFileRoute } from "@tanstack/react-router"

import { NotAvailable } from "@/components/not-available"
import { useI18n } from "@/i18n"

export const Route = createFileRoute("/tasks/$id")({
  component: TaskDetail,
})

function TaskDetail() {
  const { t } = useI18n()
  return <NotAvailable title={t("tasks.detail.title")} />
}
