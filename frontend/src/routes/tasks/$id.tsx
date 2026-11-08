import { createFileRoute } from "@tanstack/react-router"

import { QueryRegion } from "@/components/inline-state"
import { Page, PageHeader, Section } from "@/components/layout/page"
import { useI18n } from "@/i18n"
import { noData as task } from "@/lib/no-data"

export const Route = createFileRoute("/tasks/$id")({
  component: TaskDetail,
})

function TaskDetail() {
  const { t } = useI18n()
  const { id } = Route.useParams()
  return (
    <Page>
      <PageHeader title={`${t("tasks.detail.title")} ${id}`} />
      <QueryRegion
        isLoading={task.isLoading}
        error={task.error}
        onRetry={task.refetch}
        isEmpty={!task.data}
        emptyMessage={t("error.task_not_found")}
      >
        <Section title={t("tasks.detail.info")}>{null}</Section>
        <Section title={t("tasks.detail.error")}>{null}</Section>
        <Section title={t("tasks.detail.logs")}>{null}</Section>
      </QueryRegion>
    </Page>
  )
}
