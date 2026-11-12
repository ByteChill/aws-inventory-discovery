import { createFileRoute, Link } from "@tanstack/react-router"
import { z } from "zod"

import { useOverview } from "@/api/hooks"
import { BarList } from "@/components/bar-list"
import { QueryRegion } from "@/components/inline-state"
import { Page, PageHeader, Section } from "@/components/layout/page"
import { useI18n } from "@/i18n"
import { formatCount } from "@/lib/format"
import { scopeParam } from "@/lib/scope"

export const Route = createFileRoute("/")({
  validateSearch: z.object({ s: scopeParam }),
  component: Overview,
})

function Overview() {
  const { t } = useI18n()
  const stats = useOverview()
  const data = stats.data
  const empty = !data || (data.resourceTotal === 0 && data.accountTotal === 0)

  return (
    <Page>
      <PageHeader title={t("nav.overview")} />
      <QueryRegion
        isLoading={stats.isLoading}
        error={stats.error}
        onRetry={() => stats.refetch()}
        isEmpty={empty}
        emptyMessage={t("overview.empty")}
        lines={4}
      >
        {data && (
          <>
            <div className="flex flex-col gap-1">
              <div className="flex flex-wrap gap-x-10 gap-y-1">
                <Link
                  to="/resources"
                  className="text-lg font-semibold hover:text-primary"
                >
                  {formatCount(t, "resources", data.resourceTotal)}
                </Link>
                <Link
                  to="/accounts"
                  className="text-lg font-semibold hover:text-primary"
                >
                  {formatCount(t, "accounts", data.accountTotal)}
                </Link>
              </div>
              {data.accountUnreachable > 0 && (
                <Link
                  to="/accounts"
                  search={{
                    f: { accessStatus: ["access_denied", "credential_error"] },
                  }}
                  className="text-danger hover:underline"
                >
                  {t("overview.inaccessible", {
                    count: data.accountUnreachable,
                  })}
                </Link>
              )}
            </div>
            <div className="grid gap-8 md:grid-cols-2">
              <Section title={t("overview.byType")}>
                <BarList
                  filterKey="resourceType"
                  items={data.byResourceType.map((x) => ({
                    value: x.resourceType,
                    label: x.label,
                    count: x.count,
                  }))}
                />
              </Section>
              <Section title={t("overview.byEnv")}>
                <BarList
                  filterKey="accountType"
                  items={data.byAccountType.map((x) => ({
                    value: x.accountType,
                    label: x.label,
                    count: x.count,
                  }))}
                />
              </Section>
            </div>
          </>
        )}
      </QueryRegion>
    </Page>
  )
}
