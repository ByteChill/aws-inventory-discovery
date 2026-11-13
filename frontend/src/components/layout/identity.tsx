import { ChevronDown, LogOut } from "lucide-react"
import { useState } from "react"

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { useI18n } from "@/i18n"
import { basePath } from "@/lib/base-path"
import { cn } from "@/lib/utils"

/**
 * The design-stage identity is built into the frontend and only exists in a
 * development build. Production gets the identity from the internal gateway;
 * until then it has none and the area stays hidden (spec 1.5).
 */
const fakeIdentity = import.meta.env.DEV
  ? { name: "张敏", email: "zhangmin@example.com" }
  : null

/**
 * Signing out and signing in again both go back to the gateway. The real
 * gateway URL arrives with the authentication integration; until then this
 * returns to the dashboard entry point.
 */
const authUrl = import.meta.env.VITE_AUTH_URL ?? `${basePath}/`

function useExpired() {
  const [expired] = useState(
    () =>
      typeof window !== "undefined" &&
      new URLSearchParams(window.location.search).get("auth") === "expired",
  )
  return expired
}

export function IdentityMenu({
  variant = "chip",
}: {
  variant?: "chip" | "avatar"
}) {
  const { t } = useI18n()
  const expired = useExpired()
  const identity = fakeIdentity
  if (!identity) return null

  const initial = identity.name.slice(0, 1).toUpperCase()
  const label = t(expired ? "identity.expiredLabel" : "identity.menuLabel", {
    name: identity.name,
  })

  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        {variant === "chip" ? (
          <button
            type="button"
            aria-label={label}
            title={label}
            className="flex items-center gap-2 rounded-full p-1 pr-1.5 hover:bg-subtle-strong"
          >
            <Avatar initial={initial} expired={expired} size="sm" />
            <span className="max-[800px]:hidden">{identity.name}</span>
            <ChevronDown
              aria-hidden="true"
              className="size-3.5 text-muted-foreground max-[800px]:hidden"
            />
          </button>
        ) : (
          <button
            type="button"
            aria-label={label}
            title={label}
            className="grid size-[30px] place-items-center rounded-full hover:bg-subtle-strong"
          >
            <Avatar initial={initial} expired={expired} size="sm" />
          </button>
        )}
      </DropdownMenuTrigger>
      <DropdownMenuContent className="w-60">
        <div className="flex items-center gap-2.5 px-2 pt-1.5 pb-2">
          <Avatar initial={initial} expired={expired} size="lg" />
          <div className="min-w-0">
            <strong className="block truncate font-semibold">
              {identity.name}
            </strong>
            <span className="block truncate text-xs text-muted-foreground">
              {identity.email}
            </span>
          </div>
        </div>
        <p className="mx-2 mb-1 flex items-center gap-1.5 border-b pb-2 text-xs text-muted-foreground">
          <span
            aria-hidden="true"
            className={cn(
              "size-1.5 rounded-full",
              expired ? "bg-warn" : "bg-[#4f8a5b]",
            )}
          />
          {t(expired ? "identity.expired" : "identity.readonly")}
        </p>
        <DropdownMenuItem
          onSelect={() => window.location.assign(authUrl)}
          className={cn(expired && "font-semibold text-primary")}
        >
          <LogOut aria-hidden="true" className="size-4" />
          {t(expired ? "identity.signInAgain" : "identity.signOut")}
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function Avatar({
  initial,
  expired,
  size,
}: {
  initial: string
  expired: boolean
  size: "sm" | "lg"
}) {
  return (
    <span
      aria-hidden="true"
      className={cn(
        "relative grid shrink-0 place-items-center rounded-full font-semibold",
        size === "sm" ? "size-[26px] text-xs" : "size-9 text-sm",
        expired
          ? "bg-subtle-strong text-muted-foreground"
          : "bg-primary-soft text-primary",
      )}
    >
      {initial}
      {expired && (
        <span className="absolute top-0 right-0 size-2 rounded-full border-2 border-topbar bg-warn" />
      )}
    </span>
  )
}
