import { createFileRoute } from "@tanstack/react-router"

import { QueryRegion } from "@/components/inline-state"
import { Page, PageHeader, Section } from "@/components/layout/page"
import { useI18n } from "@/i18n"
import { noData as resource } from "@/lib/no-data"

// `id` is the numeric resource id, or a URL-encoded ARN from the search box;
// the data hook resolves an ARN through GET /resources/lookup.
export const Route = createFileRoute("/resources/$id")({
  component: ResourceDetail,
})

function ResourceDetail() {
  const { t } = useI18n()
  const { id } = Route.useParams()
  return (
    <Page>
      <PageHeader title={id} mono />
      <QueryRegion
        isLoading={resource.isLoading}
        error={resource.error}
        onRetry={resource.refetch}
        isEmpty={!resource.data}
        emptyMessage={t("error.resource_not_found")}
      >
        <Section title={t("detail.basic")}>{null}</Section>
        <Section title={t("detail.tags")}>{null}</Section>
        <Section title={t("detail.relationships")}>{null}</Section>
        <Section title={t("detail.configuration")}>{null}</Section>
      </QueryRegion>
    </Page>
  )
}
