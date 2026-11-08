import { createFileRoute } from "@tanstack/react-router"

import { QueryRegion } from "@/components/inline-state"
import { Page, PageHeader, Section } from "@/components/layout/page"
import { useI18n } from "@/i18n"
import { noData as scan } from "@/lib/no-data"

export const Route = createFileRoute("/scans/$id")({
  component: ScanDetail,
})

function ScanDetail() {
  const { t } = useI18n()
  const { id } = Route.useParams()
  return (
    <Page>
      <PageHeader title={`${t("scans.detail.title")} ${id}`} />
      <QueryRegion
        isLoading={scan.isLoading}
        error={scan.error}
        onRetry={scan.refetch}
        isEmpty={!scan.data}
        emptyMessage={t("error.scan_not_found")}
      >
        <Section title={t("scans.detail.scope")}>{null}</Section>
        <Section title={t("scans.detail.failuresByReason")}>{null}</Section>
        <Section title={t("scans.detail.tasks")}>{null}</Section>
      </QueryRegion>
    </Page>
  )
}
