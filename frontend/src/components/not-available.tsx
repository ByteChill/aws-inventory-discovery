import { Page, PageHeader } from "@/components/layout/page"
import { useI18n } from "@/i18n"

/** For pages that need endpoints the API does not have yet; nothing is faked. */
export function NotAvailable({ title }: { title: string }) {
  const { t } = useI18n()
  return (
    <Page>
      <PageHeader title={title} />
      <p className="text-muted-foreground">{t("page.notAvailable")}</p>
    </Page>
  )
}
