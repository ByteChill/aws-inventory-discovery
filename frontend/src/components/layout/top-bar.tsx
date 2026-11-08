import { Link, useNavigate } from "@tanstack/react-router"
import { ChevronDown } from "lucide-react"

import {
  LanguageSwitch,
  ThemeSwitch,
} from "@/components/layout/preference-menus"
import { SearchBox } from "@/components/search-box"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { useI18n } from "@/i18n"
import { cn } from "@/lib/utils"

const navLink =
  "rounded-md px-2.5 py-1 text-muted-foreground hover:bg-subtle-strong hover:text-foreground"
const navActive =
  "bg-primary-soft font-medium text-primary hover:bg-primary-soft hover:text-primary"

export function TopBar() {
  const { t } = useI18n()
  const navigate = useNavigate()
  return (
    <header className="sticky top-0 z-40 border-b bg-background">
      <div className="flex min-h-12 flex-wrap items-center gap-x-6 gap-y-2 px-6 py-2">
        <Link to="/" className="font-semibold text-foreground">
          {t("app.name")}
        </Link>
        <nav aria-label={t("nav.main")} className="flex items-center gap-1">
          <Link
            to="/"
            activeOptions={{ exact: true }}
            className={navLink}
            activeProps={{ className: cn(navLink, navActive) }}
          >
            {t("nav.overview")}
          </Link>
          <Link
            to="/resources"
            className={navLink}
            activeProps={{ className: cn(navLink, navActive) }}
          >
            {t("nav.resources")}
          </Link>
          <Link
            to="/accounts"
            className={navLink}
            activeProps={{ className: cn(navLink, navActive) }}
          >
            {t("nav.accounts")}
          </Link>
        </nav>
        <div className="ml-auto flex items-center gap-2">
          <SearchBox className="w-72" />
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost">
                {t("nav.admin")}
                <ChevronDown />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent>
              <DropdownMenuItem onSelect={() => navigate({ to: "/scans" })}>
                {t("nav.scans")}
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
          <LanguageSwitch />
          <ThemeSwitch />
        </div>
      </div>
    </header>
  )
}
