import { useSyncExternalStore } from "react"

export type Theme = "system" | "light" | "dark"
export type Locale = "zh-CN" | "en"

export const THEMES: Theme[] = ["system", "light", "dark"]
export const LOCALES: Locale[] = ["zh-CN", "en"]

export function readStorage(key: string): string | null {
  try {
    return localStorage.getItem(key)
  } catch {
    return null
  }
}

export function writeStorage(key: string, value: string | null) {
  try {
    if (value === null) localStorage.removeItem(key)
    else localStorage.setItem(key, value)
  } catch {
    // storage unavailable (private mode); preferences just won't persist
  }
}

type Store<T> = {
  get: () => T
  set: (value: T) => void
  subscribe: (listener: () => void) => () => void
}

function createStore<T extends string>(
  key: string,
  valid: readonly T[],
  fallback: T,
  onChange: (value: T) => void,
): Store<T> {
  const listeners = new Set<() => void>()
  const stored = readStorage(key)
  let value: T = valid.includes(stored as T) ? (stored as T) : fallback
  return {
    get: () => value,
    set(next) {
      value = next
      writeStorage(key, next)
      onChange(next)
      for (const l of listeners) l()
    },
    subscribe(listener) {
      listeners.add(listener)
      return () => listeners.delete(listener)
    },
  }
}

const media =
  typeof window !== "undefined"
    ? window.matchMedia("(prefers-color-scheme: dark)")
    : null

function applyTheme(theme: Theme) {
  const dark = theme === "dark" || (theme === "system" && !!media?.matches)
  document.documentElement.classList.toggle("dark", dark)
}

const themeStore = createStore<Theme>("theme", THEMES, "system", applyTheme)
const localeStore = createStore<Locale>("locale", LOCALES, "zh-CN", (l) => {
  document.documentElement.lang = l
})

/** Apply the stored theme and language before the first render. */
export function initPreferences() {
  applyTheme(themeStore.get())
  document.documentElement.lang = localeStore.get()
  media?.addEventListener("change", () => applyTheme(themeStore.get()))
}

function useStore<T>(store: Store<T>) {
  return useSyncExternalStore(store.subscribe, store.get, store.get)
}

export const useTheme = () => [useStore(themeStore), themeStore.set] as const
export const useLocale = () => [useStore(localeStore), localeStore.set] as const
export const getLocale = () => localeStore.get()
