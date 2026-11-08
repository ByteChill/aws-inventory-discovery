/**
 * A small typed i18n layer instead of react-i18next: the UI has one flat set of
 * messages, two locales and simple plurals, so a ~60 line context with
 * compile-time key checking is enough and avoids a runtime dependency.
 *
 * Messages live in flat JSON files. Interpolation is `{name}`; numbers are
 * formatted with thousands separators. A message with ` | ` has a singular and
 * a plural form, chosen by `count` (used for English).
 */
import { createContext, type ReactNode, useContext, useMemo } from "react"
import { type Locale, useLocale } from "@/lib/preferences"
import en from "@/locales/en.json"
import zhCN from "@/locales/zh-CN.json"

export type MessageKey = keyof typeof zhCN
// Compile-time check that both locales define exactly the same keys.
const messages: Record<Locale, Record<MessageKey, string>> = {
  "zh-CN": zhCN,
  en,
}

export type Params = Record<string, string | number>
export type TFunction = (key: MessageKey, params?: Params) => string

export function createT(locale: Locale): TFunction {
  const nf = new Intl.NumberFormat(locale)
  return (key, params) => {
    let text = messages[locale][key] ?? key
    if (text.includes(" | ")) {
      const [one, other] = text.split(" | ")
      text = params?.count === 1 ? one : other
    }
    if (!params) return text
    return text.replace(/\{(\w+)\}/g, (_, name: string) => {
      const v = params[name]
      if (v === undefined) return `{${name}}`
      return typeof v === "number" ? nf.format(v) : v
    })
  }
}

type I18n = { locale: Locale; t: TFunction; setLocale: (l: Locale) => void }
const Context = createContext<I18n | null>(null)

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocale] = useLocale()
  const value = useMemo(
    () => ({ locale, setLocale, t: createT(locale) }),
    [locale, setLocale],
  )
  return <Context.Provider value={value}>{children}</Context.Provider>
}

export function useI18n() {
  const ctx = useContext(Context)
  if (!ctx) throw new Error("useI18n must be used inside I18nProvider")
  return ctx
}
