import { createFileRoute } from "@tanstack/react-router"
import { QueryRegion } from "@/components/inline-state"
import { Page, PageHeader, Section } from "@/components/layout/page"
import { SearchBox } from "@/components/search-box"
import { useI18n } from "@/i18n"
import { noData as stats } from "@/lib/no-data"

export const Route = createFileRoute("/")({
  component: Overview,
})

function Overview() {
  const { t } = useI18n()
  return (
    <Page>
      <PageHeader title={t("nav.overview")} />
      <SearchBox size="large" className="max-w-2xl" />
      <QueryRegion
        isLoading={stats.isLoading}
        error={stats.error}
        onRetry={stats.refetch}
        isEmpty={!stats.data}
        emptyMessage={t("overview.empty")}
        lines={2}
      />
      <div className="grid gap-8 md:grid-cols-2">
        <Section title={t("overview.byType")}>
          <QueryRegion
            isLoading={stats.isLoading}
            error={stats.error}
            onRetry={stats.refetch}
            isEmpty={!stats.data}
            emptyMessage={t("overview.empty")}
          />
        </Section>
        <Section title={t("overview.byEnv")}>
          <QueryRegion
            isLoading={stats.isLoading}
            error={stats.error}
            onRetry={stats.refetch}
            isEmpty={!stats.data}
            emptyMessage={t("overview.empty")}
          />
        </Section>
      </div>
    </Page>
  )
}
