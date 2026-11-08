import { createFileRoute } from "@tanstack/react-router"

import { QueryRegion } from "@/components/inline-state"
import { Page, PageHeader, Section } from "@/components/layout/page"
import { useI18n } from "@/i18n"
import { noData as account } from "@/lib/no-data"

export const Route = createFileRoute("/accounts/$accountId")({
  component: AccountDetail,
})

function AccountDetail() {
  const { t } = useI18n()
  const { accountId } = Route.useParams()
  return (
    <Page>
      <PageHeader title={accountId} mono />
      <QueryRegion
        isLoading={account.isLoading}
        error={account.error}
        onRetry={account.refetch}
        isEmpty={!account.data}
        emptyMessage={t("error.account_not_found")}
      >
        <Section title={t("accounts.basic")}>{null}</Section>
        <Section title={t("accounts.regions")}>{null}</Section>
        <Section title={t("accounts.matrix")}>{null}</Section>
      </QueryRegion>
    </Page>
  )
}
