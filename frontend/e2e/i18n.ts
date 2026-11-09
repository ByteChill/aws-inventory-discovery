import { readFileSync } from "node:fs"

const load = (name: string) =>
  JSON.parse(
    readFileSync(
      new URL(`../src/locales/${name}.json`, import.meta.url),
      "utf8",
    ),
  ) as Record<string, string>

const tables = { "zh-CN": load("zh-CN"), en: load("en") }

/** The dashboard's own copy, so the tests follow the locale files instead of repeating strings. */
export function messages(locale: "zh-CN" | "en") {
  const table = tables[locale]
  return (key: string, params: Record<string, string | number> = {}) => {
    let text = table[key]
    if (text === undefined) throw new Error(`missing message ${key}`)
    // "singular | plural", chosen by count (as in the app's own i18n layer).
    if (text.includes(" | ")) {
      const [one, other] = text.split(" | ")
      text = params.count === 1 ? one : other
    }
    return text.replace(/\{(\w+)\}/g, (_, name: string) => {
      const v = params[name]
      return typeof v === "number" ? v.toLocaleString(locale) : String(v)
    })
  }
}

export const t = messages("zh-CN")
