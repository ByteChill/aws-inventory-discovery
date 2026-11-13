// Applies the theme and language before first paint to avoid a flash.
// Keep in sync with src/lib/preferences.ts.
;(() => {
  try {
    const root = document.documentElement
    const theme = localStorage.getItem("theme") || "system"
    const dark =
      theme === "dark" ||
      (theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches)
    if (dark) root.classList.add("dark")
    root.lang = localStorage.getItem("locale") || "zh-CN"
  } catch {
    // storage unavailable: defaults apply
  }
})()
