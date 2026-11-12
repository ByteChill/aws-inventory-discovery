import { createRootRoute, Link, Outlet } from "@tanstack/react-router"
import { useEffect, useState } from "react"

import { Page } from "@/components/layout/page"
import { Sidebar } from "@/components/layout/sidebar"
import { TopBar } from "@/components/layout/top-bar"
import { Button } from "@/components/ui/button"
import { useI18n } from "@/i18n"

export const Route = createRootRoute({
  component: Root,
  notFoundComponent: NotFound,
})

function Root() {
  const { t } = useI18n()
  const [navOpen, setNavOpen] = useState(false)

  useEffect(() => {
    document.title = t("app.title")
  }, [t])

  return (
    <div className="min-h-dvh">
      <Sidebar open={navOpen} onClose={() => setNavOpen(false)} />
      <div className="ml-[264px] min-w-0 max-[1180px]:ml-[224px] max-[800px]:ml-0">
        <TopBar onOpenNav={() => setNavOpen(true)} />
        <main className="min-w-0">
          <Outlet />
        </main>
      </div>
    </div>
  )
}

function NotFound() {
  const { t } = useI18n()
  return (
    <Page>
      <h1>{t("notFound.title")}</h1>
      <Button asChild variant="outline" className="self-start">
        <Link to="/">{t("action.backToOverview")}</Link>
      </Button>
    </Page>
  )
}
