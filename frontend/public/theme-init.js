// Applies theme, palette and language before first paint to avoid a flash.
// Keep in sync with src/lib/preferences.ts.
;(() => {
  try {
    const root = document.documentElement
    const fromUrl = new URLSearchParams(location.search).get("palette")
    const theme = localStorage.getItem("theme") || "system"
    const palette = ["dai-lv", "dai-lan", "yan-zhi"].includes(fromUrl)
      ? fromUrl
      : localStorage.getItem("palette")
    const dark =
      theme === "dark" ||
      (theme === "system" && matchMedia("(prefers-color-scheme: dark)").matches)
    if (dark) root.classList.add("dark")
    if (palette) root.dataset.palette = palette
    root.lang = localStorage.getItem("locale") || "zh-CN"
  } catch {
    // storage unavailable: defaults apply
  }
})()
