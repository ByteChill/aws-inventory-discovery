import { PanelLeft, Search } from "lucide-react"
import { useCallback, useEffect, useRef, useState } from "react"

import { GlobalSearch } from "@/components/layout/global-search"
import { IdentityMenu } from "@/components/layout/identity"
import {
  LanguageSwitch,
  ThemeSwitch,
} from "@/components/layout/preference-menus"
import { useI18n } from "@/i18n"
import { cn } from "@/lib/utils"

/** How far the page scrolls before the top bar folds away into a small capsule. */
const FOLD_AT = 56

export function TopBar({ onOpenNav }: { onOpenNav: () => void }) {
  const { t } = useI18n()
  const [barOpen, setBarOpen] = useState(false)
  const [scrolled, setScrolled] = useState(
    () => typeof window !== "undefined" && window.scrollY > FOLD_AT,
  )
  const headerRef = useRef<HTMLElement>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const searchFabRef = useRef<HTMLButtonElement>(null)

  useEffect(() => {
    const onScroll = () => {
      const next = window.scrollY > FOLD_AT
      setScrolled(next)
      if (!next) setBarOpen(false)
    }
    window.addEventListener("scroll", onScroll, { passive: true })
    return () => window.removeEventListener("scroll", onScroll)
  }, [])

  const closeBar = useCallback((restoreFocus = true) => {
    setBarOpen(false)
    if (restoreFocus)
      requestAnimationFrame(() =>
        searchFabRef.current?.focus({ preventScroll: true }),
      )
  }, [])

  useEffect(() => {
    document.body.dataset.barOpen = String(barOpen)
    if (barOpen)
      requestAnimationFrame(() =>
        inputRef.current?.focus({ preventScroll: true }),
      )
  }, [barOpen])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null
      if (e.key === "/" && !e.metaKey && !e.ctrlKey && !e.altKey) {
        if (target?.closest("input,textarea,select,[contenteditable='true']"))
          return
        if (document.querySelector("dialog[open]")) return
        e.preventDefault()
        if (window.scrollY > FOLD_AT) setBarOpen(true)
        else inputRef.current?.focus({ preventScroll: true })
        return
      }
      if (e.key === "Escape" && document.body.dataset.barOpen === "true") {
        e.preventDefault()
        closeBar()
      }
    }
    document.addEventListener("keydown", onKey)
    return () => document.removeEventListener("keydown", onKey)
  }, [closeBar])

  useEffect(() => {
    if (!barOpen) return
    const onPointerDown = (e: PointerEvent) => {
      if (headerRef.current?.contains(e.target as Node)) return
      if ((e.target as HTMLElement | null)?.closest("[role='menu']")) return
      closeBar()
    }
    document.addEventListener("pointerdown", onPointerDown)
    return () => document.removeEventListener("pointerdown", onPointerDown)
  }, [barOpen, closeBar])

  function openBar() {
    setBarOpen(true)
  }

  return (
    <>
      <header
        ref={headerRef}
        className={cn(
          "relative z-20 flex h-14 items-center gap-3 border-b border-border bg-topbar px-8 max-[1180px]:px-5 max-[800px]:px-4",
          barOpen && "sticky top-0 z-30 animate-bar-in shadow-pop",
        )}
      >
        <button
          type="button"
          onClick={onOpenNav}
          aria-label={t("nav.expand")}
          className="hidden size-8 shrink-0 place-items-center rounded-md text-muted-foreground hover:bg-subtle-strong max-[800px]:grid"
        >
          <PanelLeft className="size-4" />
        </button>
        <GlobalSearch
          inputRef={inputRef}
          onSubmitted={() => setBarOpen(false)}
        />
        <div className="ml-auto flex shrink-0 items-center gap-1">
          <LanguageSwitch />
          <ThemeSwitch />
          <IdentityMenu />
        </div>
      </header>

      {scrolled && !barOpen && (
        <>
          <div className="fixed top-2.5 right-2 z-30 flex items-center gap-0.5 rounded-full border border-[#d8d0c8] bg-surface p-0.5 shadow-float dark:border-border">
            <button
              ref={searchFabRef}
              type="button"
              onClick={openBar}
              aria-label={t("search.globalLabel")}
              title={t("search.shortcut")}
              aria-expanded={false}
              className="grid size-[30px] place-items-center rounded-full hover:bg-subtle-strong"
            >
              <Search className="size-4" />
            </button>
            <IdentityMenu variant="avatar" />
          </div>
          <button
            type="button"
            onClick={onOpenNav}
            aria-label={t("nav.expand")}
            className="fixed top-2.5 left-2 z-30 hidden size-9 place-items-center rounded-full border border-[#d8d0c8] bg-surface shadow-float hover:bg-subtle-strong max-[800px]:grid dark:border-border"
          >
            <PanelLeft className="size-4" />
          </button>
        </>
      )}
    </>
  )
}
