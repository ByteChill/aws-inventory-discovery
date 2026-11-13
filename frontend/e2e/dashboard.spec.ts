import { expect, type Page, test } from "@playwright/test"
import { BASE_PATH } from "./base-url"
import { messages, t } from "./i18n"

// All expected numbers and names come from the stack's own API; the copy comes from the locale files.

type Page_<T> = { items: T[]; total: number }
type Resource = {
  id: number
  resourceType: string
  resourceName: string | null
  resourceId: string
  arn: string
  accountId: string
  awsRegion: string
}
type Account = { accountId: string; accountName: string }
type Column = { key: string; label: string; scope: string; type: string }
type Facets = Record<string, { value: string; label: string; count: number }[]>
type Scope = { scanShardId: number; candidateCount: number }
type Candidate = {
  id: number | null
  resourceId: string
  resourceName: string | null
}

const TYPE = "AWS::EC2::Subnet"
const ENUM_COLUMN = "availabilityZone"
const TAG_KEY = "Owner"
// The service menu names AWS services as AWS does (dashboard.md 5).
const SQS = { label: "SQS", type: "AWS::SQS::Queue" }

async function api<T>(page: Page, path: string): Promise<T> {
  const res = await page.request.get(`api/v1${path}`)
  expect(res.ok(), `${path}: ${res.status()}`).toBeTruthy()
  return (await res.json()) as T
}

const section = (page: Page, title: string) =>
  page
    .locator("section")
    .filter({ has: page.getByRole("heading", { name: title, level: 2 }) })

const count = (n: number) => n.toLocaleString("zh-CN")
const totalText = (n: number) => t("table.total.resources", { count: n })

test("概览：统计数字和按服务分布打开对应的资源列表", async ({ page }) => {
  // Live list endpoints, not the (cached) overview, give the expected numbers.
  const resources = await api<Page_<Resource>>(page, "/resources?pageSize=1")
  const sqs = await api<Page_<Resource>>(
    page,
    `/resources?pageSize=1&resourceType=${encodeURIComponent(SQS.type)}`,
  )

  await page.goto("./")
  const stats = page.locator(`dl[aria-label="${t("nav.overview")}"]`)
  await expect(
    stats.getByRole("link", { name: count(resources.total), exact: true }),
  ).toBeVisible()

  // The prototype's service bars sit on the page; a bar opens that service's list.
  const services = page.locator("section").filter({
    has: page.getByRole("heading", { name: t("overview.byService") }),
  })
  // A visible bar opens its list directly.
  const visibleBar = services.getByRole("link").first()
  const visibleService = (await visibleBar.innerText()).split("\n")[0].trim()
  await visibleBar.click()
  await expect(page).toHaveURL(/\/resources\?/)
  await expect(
    page.getByRole("heading", { name: visibleService, level: 1 }),
  ).toBeVisible()

  // Panels show four rows; the rest sit behind the expand control.
  await page.goto("./")
  const byService = page.locator("section").filter({
    has: page.getByRole("heading", { name: t("overview.byService") }),
  })
  await byService
    .getByRole("button", {
      name: new RegExp(t("overview.showAll", { count: "\\d+" })),
    })
    .click()
  await byService
    .getByRole("link", { name: new RegExp(`^${SQS.label}\\b`) })
    .click()
  await expect(page).toHaveURL(/\/resources\?/)
  await expect(
    page.getByRole("heading", { name: SQS.label, level: 1 }),
  ).toBeVisible()
  await expect(page.getByText(totalText(sqs.total))).toBeVisible()
})

test("资源：筛选行、类型专属列、更多筛选和导出链接", async ({ page }) => {
  const columns = await api<Column[]>(
    page,
    `/columns?resourceType=${encodeURIComponent(TYPE)}`,
  )
  const enumColumn = columns.find((c) => c.key === ENUM_COLUMN) as Column
  expect(enumColumn.scope).toBe("type")
  const everything = await api<Page_<Resource>>(page, "/resources?pageSize=1")
  const ofType = await api<Page_<Resource>>(
    page,
    `/resources?pageSize=1&resourceType=${encodeURIComponent(TYPE)}`,
  )

  await page.goto("resources")
  await expect(page.getByText(totalText(everything.total))).toBeVisible()
  // The type-specific column is not offered until exactly one type is chosen.
  await expect(
    page.getByRole("columnheader", { name: enumColumn.label }),
  ).toHaveCount(0)

  await page
    .getByRole("combobox", { name: t("resources.group.resourceType") })
    .selectOption(TYPE)
  await expect(page.getByText(totalText(ofType.total))).toBeVisible()
  await expect(
    page.getByRole("columnheader", { name: enumColumn.label }),
  ).toBeVisible()

  // An enum column filter, under "more filters", narrows the total to the API's count.
  const facets = await api<Facets>(
    page,
    `/resources/facets?field=attr.${ENUM_COLUMN}&resourceType=${encodeURIComponent(TYPE)}`,
  )
  const bucket = facets[`attr.${ENUM_COLUMN}`][0]
  expect(bucket.count).toBeLessThan(ofType.total)
  await page.getByRole("button", { name: t("resources.filter.more") }).click()
  const more = page.getByRole("dialog")
  await more.getByRole("checkbox", { name: new RegExp(bucket.label) }).check()
  await expect(page.getByText(totalText(bucket.count))).toBeVisible()

  // Tag missing: the UI total equals the API's for the same filters.
  const missing = await api<Page_<Resource>>(
    page,
    `/resources?pageSize=1&resourceType=${encodeURIComponent(TYPE)}&tagMissing=${TAG_KEY}&attr.${ENUM_COLUMN}=${bucket.value}`,
  )
  await more.getByRole("radio", { name: t("resources.tags.missing") }).click()
  await more.getByLabel(t("resources.tags.key")).fill(TAG_KEY)
  await more.getByRole("button", { name: t("action.add") }).click()
  await expect(page.getByText(totalText(missing.total))).toBeVisible()
  await page.keyboard.press("Escape")

  // Export links carry the current filters and the language.
  await page.getByRole("button", { name: t("action.export") }).click()
  for (const name of [t("action.exportCsv"), t("action.exportJsonl")]) {
    const href = await page.getByRole("menuitem", { name }).getAttribute("href")
    const url = new URL(href as string, "http://x")
    expect(url.pathname).toBe(`${BASE_PATH}/api/v1/resources/export`)
    expect(url.searchParams.get("resourceType")).toBe(TYPE)
    expect(url.searchParams.get("tagMissing")).toBe(TAG_KEY)
    expect(url.searchParams.get(`attr.${ENUM_COLUMN}`)).toBe(bucket.value)
    expect(url.searchParams.get("lang")).toBe("zh-CN")
  }
})

test("筛选：改变筛选后停在原位置，不跳回顶部", async ({ page }) => {
  await page.setViewportSize({ width: 1200, height: 800 })
  await page.goto(
    `resources?pageSize=50&f=${encodeURIComponent(JSON.stringify({ resourceType: [SQS.type] }))}`,
  )
  await expect(
    page.getByRole("button", { name: /^eu-west-1/ }).first(),
  ).toBeVisible()

  // A little way down the page: the region pills stay on screen, so opening
  // the filter does not itself scroll.
  await page.mouse.wheel(0, 120)
  await page.waitForTimeout(300)
  const before = await page.evaluate(() => window.scrollY)
  expect(before).toBeGreaterThan(0)

  await page
    .getByRole("button", { name: /^eu-west-1/ })
    .first()
    .click()
  await expect(page).toHaveURL(/awsRegion/)
  await expect
    .poll(async () =>
      Math.abs((await page.evaluate(() => window.scrollY)) - before),
    )
    .toBeLessThan(4)
})

test("资源预览和详情：整行打开预览，详情回到原列表，刷新更新时间", async ({
  page,
}) => {
  const r = (
    await api<Page_<Resource>>(
      page,
      `/resources?pageSize=1&resourceType=${encodeURIComponent(SQS.type)}`,
    )
  ).items[0]
  const name = r.resourceName ?? r.resourceId

  await page.goto("resources")
  const search = page.getByRole("searchbox", {
    name: t("resources.filter.searchPlaceholder"),
  })
  await search.fill(name)
  await search.press("Enter")
  await expect(page).toHaveURL(/q=/)

  // A click outside the name link opens the preview drawer.
  const row = page
    .getByRole("row", { name: t("preview.row", { name }) })
    .first()
  await row.getByRole("cell").nth(3).click()
  const preview = page.getByRole("dialog", { name: t("preview.title") })
  await expect(preview.getByRole("heading", { name })).toBeVisible()
  await expect(preview.getByText(r.arn)).toBeVisible()
  await preview.getByRole("link", { name: t("preview.viewDetail") }).click()

  await expect(page).toHaveURL(new RegExp(`/resources/${r.id}\\?back=`))
  await expect(page.getByRole("heading", { name, level: 1 })).toBeVisible()
  await expect(
    section(page, t("detail.identifiers")).getByText(r.arn),
  ).toBeVisible()

  // 刷新 re-reads the resource live; the "更新于" time moves forward.
  const updated = () =>
    page
      .getByText(/^更新于/)
      .locator("time")
      .getAttribute("datetime")
  const before = await updated()
  await page.getByRole("button", { name: t("action.refresh") }).click()
  await expect.poll(updated).not.toBe(before)

  // The back link returns to the list with its search.
  await page.getByRole("link", { name: t("detail.backToList") }).click()
  await expect(page).toHaveURL(/\/resources\?.*q=/)
  await expect(row).toBeVisible()
})

test("扫描：从表单发起单个账号的扫描，详情成功，当前范围资源和任务详情可打开", async ({
  page,
}) => {
  const account = (await api<Page_<Account>>(page, "/accounts?pageSize=1"))
    .items[0]

  // From a scoped resource list, scan management offers the way back to that scope.
  await page.goto(`./?s=acct:${account.accountId}`)
  await page
    .getByRole("navigation", { name: t("nav.main") })
    .getByRole("link", { name: new RegExp(`^${t("scans.title")}`) })
    .click()
  await expect(
    page.getByRole("heading", { name: t("scans.title"), level: 1 }),
  ).toBeVisible()
  const returnScope = page.getByRole("link", {
    name: t("scans.returnScope", { name: account.accountName }),
  })
  await expect(returnScope).toBeVisible()
  await returnScope.click()
  await expect(page).toHaveURL(
    (url) => url.searchParams.get("s") === `acct:${account.accountId}`,
  )

  await page.goto("scans")
  await expect(
    page.getByRole("region", { name: t("scans.summary.label") }),
  ).toBeVisible()
  await expect(
    page.getByRole("columnheader", { name: t("scans.column.id"), exact: true }),
  ).toBeVisible()
  await page.getByRole("button", { name: t("scans.new"), exact: true }).click()
  const dialog = page.getByRole("dialog")
  await dialog
    .getByLabel(t("scans.form.accountsSearch"))
    .fill(account.accountId)
  await dialog.getByRole("checkbox", { name: account.accountName }).check()
  await dialog
    .getByRole("button", { name: t("action.scan"), exact: true })
    .click()

  await expect(page).toHaveURL(/\/scans\/\d+/)
  const scanId = Number(new URL(page.url()).pathname.split("/").pop())
  await expect(
    page.getByRole("heading", {
      level: 1,
      name: new RegExp(`^${t("scans.detail.title")} #\\d+$`),
    }),
  ).toBeVisible()
  await expect(page.getByText(t("scans.status.succeeded")).first()).toBeVisible(
    { timeout: 90_000 },
  )

  // Result numbers are statistics; the scope's current resources are one link away.
  await page.getByRole("link", { name: t("scans.viewScopeResources") }).click()
  await expect(page).toHaveURL(new RegExp(`scanRequestId=${scanId}`))
  await expect(
    page.getByRole("heading", {
      name: t("resources.scanTitle", { id: scanId }),
    }),
  ).toBeVisible()
  await page
    .getByRole("link", { name: t("resources.backToScan", { id: scanId }) })
    .click()

  await page
    .getByRole("table")
    .getByRole("link", { name: /^#?\d+$/ })
    .first()
    .click()
  await expect(page).toHaveURL(/\/tasks\/\d+/)
  await expect(
    page.getByRole("heading", { name: new RegExp(t("tasks.detail.title")) }),
  ).toBeVisible()
  await expect(
    page.getByText(t("tasks.status.succeeded")).first(),
  ).toBeVisible()
  await expect(
    page.getByText(
      new RegExp(`${t("tasks.logsUnavailable")}|${t("tasks.noLogs")}`),
    ),
  ).toBeVisible()
})

test("账号：搜索后整行进入资源范围，覆盖表和账号范围的资源", async ({
  page,
}) => {
  const account = (await api<Page_<Account>>(page, "/accounts?pageSize=1"))
    .items[0]
  const owned = await api<Page_<Resource>>(
    page,
    `/resources?pageSize=1&accountId=${account.accountId}`,
  )

  await page.goto("accounts")
  await page
    .getByRole("searchbox", { name: t("accounts.searchPlaceholder") })
    .fill(account.accountId)
  await expect(
    page.getByText(t("table.total.accounts", { count: 1 })),
  ).toBeVisible()
  const row = page.getByRole("row", {
    name: t("accounts.openRow", { name: account.accountName }),
  })
  await row.getByRole("cell").nth(1).click()

  // The row opens the account's own resource scope, not the coverage page.
  await expect
    .poll(() => new URL(page.url()).searchParams.get("s"))
    .toBe(`acct:${account.accountId}`)
  await expect(
    page.getByRole("heading", { name: account.accountName, level: 1 }),
  ).toBeVisible()

  // Coverage stays one click away, in the accounts list's 扫描情况 column.
  await page.goBack()
  await row.getByRole("link", { name: t("accounts.coverage") }).click()
  await expect(
    page.getByRole("heading", {
      name: t("accounts.coverageTitle", { name: account.accountName }),
      level: 1,
    }),
  ).toBeVisible()
  // The matrix is a secondary block, collapsed until asked for.
  await page
    .locator("summary")
    .filter({ hasText: t("accounts.matrix") })
    .click()
  await expect(
    page.getByRole("img", { name: t("accounts.cell.ok") }).first(),
  ).toBeVisible()

  await page.getByRole("link", { name: t("accounts.viewResources") }).click()
  await expect(page).toHaveURL(/s=acct/)
  await expect(page.getByText(totalText(owned.total))).toBeVisible()
})

test("待核对的范围：从扫描管理进入，预览候选资源，暂不确认回到扫描管理", async ({
  page,
}) => {
  const scopes = await api<Page_<Scope>>(page, "/scan-scopes?pageSize=200")
  test.skip(scopes.total === 0, "这个栈里没有触发范围保护的范围")

  await page.goto("scans")
  await section(page, t("scans.anomalies"))
    .getByRole("link", { name: t("scans.reviewResources") })
    .first()
    .click()
  await expect(page).toHaveURL(/\/scan-scopes\/\d+/)
  const scopeId = Number(new URL(page.url()).pathname.split("/").pop())
  await expect(
    page.getByRole("heading", { name: t("scanScope.title"), level: 1 }),
  ).toBeVisible()
  await expect(page.getByText(t("scanScope.noteTitle"))).toBeVisible()

  // The saved candidates, when the guard kept them: a row opens the preview, whose
  // detail link comes back to this page.
  const candidates = await api<Page_<Candidate>>(
    page,
    `/scan-scopes/${scopeId}/candidates?pageSize=1`,
  )
  const first = candidates.items[0]
  if (first?.id != null) {
    const name = first.resourceName ?? first.resourceId
    await expect(
      page.getByText(t("table.total.candidates", { count: candidates.total })),
    ).toBeVisible()
    await page
      .getByRole("row", { name: t("preview.row", { name }) })
      .getByRole("cell")
      .nth(2)
      .click()
    const preview = page.getByRole("dialog", { name: t("preview.title") })
    const detail = preview.getByRole("link", { name: t("preview.viewDetail") })
    expect(await detail.getAttribute("href")).toContain(
      `back=${encodeURIComponent(`/scan-scopes/${scopeId}`)}`,
    )
    await page.keyboard.press("Escape")
    await expect(preview).toBeHidden()
  }

  // The irreversible step asks once; cancelling changes nothing.
  await page.getByRole("button", { name: t("action.confirmChange") }).click()
  const confirm = page.getByRole("dialog", { name: t("action.confirmChange") })
  await expect(confirm.getByText(t("scanScope.confirmNote"))).toBeVisible()
  await confirm.getByRole("button", { name: t("action.cancel") }).click()
  await expect(confirm).toBeHidden()

  await page.getByRole("link", { name: t("scanScope.hold") }).click()
  await expect(page).toHaveURL(/\/scans$/)
})

test("语言切换到 English、主题切换到暗色后页面仍可用", async ({ page }) => {
  const en = messages("en")
  await page.goto("resources")
  await page.getByRole("button", { name: t("lang.label") }).click()
  await page.getByRole("menuitemradio", { name: t("lang.en") }).click()
  await expect(page.locator("html")).toHaveAttribute("lang", "en")
  const total = (await api<Page_<Resource>>(page, "/resources?pageSize=1"))
    .total
  await expect(
    page.getByText(en("table.total.resources", { count: total })),
  ).toBeVisible()

  await page.getByRole("button", { name: en("theme.label") }).click()
  await page.getByRole("menuitemradio", { name: en("theme.dark") }).click()
  await expect(page.locator("html")).toHaveClass(/dark/)
  await page
    .getByRole("navigation", { name: en("nav.main") })
    .getByRole("link", { name: new RegExp(`^${en("nav.overview")}`) })
    .click()
  await expect(
    page.getByRole("heading", { name: en("nav.overview"), level: 1 }),
  ).toBeVisible()
})

// dashboard.md 6: check 1440 and 1920, plus 1024 and 390 for the collapsed menu,
// tables that scroll sideways and dialogs that stay on screen.
for (const width of [1440, 1920, 1024, 390]) {
  test(`宽度 ${width}：页面不横向溢出，菜单和扫描表单放得下`, async ({
    page,
  }) => {
    await page.setViewportSize({ width, height: 900 })
    const account = (await api<Page_<Account>>(page, "/accounts?pageSize=1"))
      .items[0]
    const total = (await api<Page_<Resource>>(page, "/resources?pageSize=1"))
      .total
    const narrow = width <= 800

    for (const path of [
      "./",
      "apps",
      "resources",
      "accounts",
      `accounts/${account.accountId}`,
      "scans",
    ]) {
      await page.goto(path)
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible()
      await page.waitForLoadState("networkidle")
      const scrollWidth = await page.evaluate(
        () => document.documentElement.scrollWidth,
      )
      expect(scrollWidth, `${path} at ${width}`).toBeLessThanOrEqual(width)
    }

    // Wide tables scroll inside their own frame instead of widening the page.
    await page.goto("resources")
    await expect(page.getByText(totalText(total))).toBeVisible()
    if (narrow) {
      const scrolls = await page
        .getByRole("table")
        .first()
        .evaluate((table) => {
          let el: HTMLElement | null = table.parentElement
          while (el && el.scrollWidth <= el.clientWidth) el = el.parentElement
          return !!el && el !== document.documentElement
        })
      expect(scrolls).toBe(true)
    }

    // The menu is a drawer on narrow screens and always shown otherwise.
    const nav = page.getByRole("navigation", { name: t("nav.main") })
    if (narrow) {
      await expect(nav).not.toBeInViewport()
      await page
        .getByRole("button", { name: t("nav.expand") })
        .first()
        .click()
      await expect(nav).toBeInViewport()
      await page
        .getByRole("button", { name: t("nav.close") })
        .first()
        .click()
      await expect(nav).not.toBeInViewport()
    } else {
      await expect(nav).toBeInViewport()
    }

    await page.goto("scans")
    await page
      .getByRole("button", { name: t("scans.new"), exact: true })
      .click()
    const box = await page.getByRole("dialog").boundingBox()
    expect(box).not.toBeNull()
    expect(box?.x ?? -1).toBeGreaterThanOrEqual(0)
    expect((box?.x ?? 0) + (box?.width ?? 0)).toBeLessThanOrEqual(width)
    await page.keyboard.press("Escape")
  })
}
