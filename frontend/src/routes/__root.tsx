import { createRootRoute, Link, Outlet } from "@tanstack/react-router"
import { useEffect } from "react"

import { TopBar } from "@/components/layout/top-bar"
import { Button } from "@/components/ui/button"
import { useI18n } from "@/i18n"

export const Route = createRootRoute({
  component: Root,
  notFoundComponent: NotFound,
})

function Root() {
  const { t } = useI18n()
  useEffect(() => {
    document.title = t("app.title")
  }, [t])
  return (
    <div className="flex h-dvh flex-col">
      <TopBar />
      <main className="min-h-0 flex-1 overflow-auto">
        <Outlet />
      </main>
    </div>
  )
}

function NotFound() {
  const { t } = useI18n()
  return (
    <div className="flex flex-col items-start gap-3 p-6">
      <h1>{t("notFound.title")}</h1>
      <Button asChild variant="outline">
        <Link to="/">{t("action.backToOverview")}</Link>
      </Button>
    </div>
  )
}
