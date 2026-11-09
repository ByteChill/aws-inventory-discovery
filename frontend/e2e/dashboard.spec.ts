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
  accountId: string
  awsRegion: string
  observedAt?: string
}
type Account = { accountId: string; accountName: string }
type Column = { key: string; label: string; scope: string; type: string }
type Facets = Record<string, { value: string; label: string; count: number }[]>

const TYPE = "AWS::EC2::Subnet"
const ENUM_COLUMN = "availabilityZone"
const TAG_KEY = "Owner"

async function api<T>(page: Page, path: string): Promise<T> {
  const res = await page.request.get(`api/v1${path}`)
  expect(res.ok(), `${path}: ${res.status()}`).toBeTruthy()
  return (await res.json()) as T
}

const section = (page: Page, title: string) =>
  page
    .locator("section")
    .filter({ has: page.getByRole("heading", { name: title, level: 2 }) })

const totalText = (n: number) => t("table.total.resources", { count: n })

test("概览：总数和分布链接打开筛选后的资源列表", async ({ page }) => {
  // Live list endpoints, not the (cached) overview, give the expected numbers.
  const resources = await api<Page_<Resource>>(page, "/resources?pageSize=1")
  const accounts = await api<Page_<Account>>(page, "/accounts?pageSize=1")
  const ofType = await api<Page_<Resource>>(
    page,
    `/resources?pageSize=1&resourceType=${encodeURIComponent(TYPE)}`,
  )
  const typeLabel = (
    await api<{ typeName: string; label: string }[]>(page, "/resource-types")
  ).find((x) => x.typeName === TYPE)?.label as string

  await page.goto("./")
  await expect(
    page.getByRole("link", {
      name: t("unit.resources", { count: resources.total }),
    }),
  ).toBeVisible()
  await expect(
    page.getByRole("link", {
      name: t("unit.accounts", { count: accounts.total }),
    }),
  ).toBeVisible()

  await page.getByRole("link", { name: typeLabel }).first().click()
  await expect(page).toHaveURL(/\/resources/)
  await expect(page.getByText(totalText(ofType.total))).toBeVisible()
})

test("资源：类型专属列、列筛选、标签缺少和导出链接", async ({ page }) => {
  const columns = await api<Column[]>(
    page,
    `/columns?resourceType=${encodeURIComponent(TYPE)}`,
  )
  const enumColumn = columns.find((c) => c.key === ENUM_COLUMN)
  expect(enumColumn?.scope).toBe("type")
  const typeLabel = (
    await api<{ typeName: string; label: string }[]>(page, "/resource-types")
  ).find((x) => x.typeName === TYPE)?.label as string

  const everything = await api<Page_<Resource>>(page, "/resources?pageSize=1")
  const ofType = await api<Page_<Resource>>(
    page,
    `/resources?pageSize=1&resourceType=${encodeURIComponent(TYPE)}`,
  )

  await page.goto("resources")
  await expect(page.getByText(totalText(everything.total))).toBeVisible()
  // The type-specific column is not offered until exactly one type is chosen.
  await expect(
    page.getByRole("columnheader", { name: enumColumn?.label }),
  ).toHaveCount(0)

  const sidebar = page.getByRole("complementary", {
    name: t("resources.filters"),
  })
  await sidebar.getByRole("checkbox", { name: typeLabel }).check()
  await expect(page.getByText(totalText(ofType.total))).toBeVisible()
  await expect(
    page.getByRole("columnheader", { name: enumColumn?.label }),
  ).toBeVisible()

  // An enum column filter narrows the total to what the API reports for that value.
  const facets = await api<Facets>(
    page,
    `/resources/facets?field=attr.${ENUM_COLUMN}&resourceType=${encodeURIComponent(TYPE)}`,
  )
  const bucket = facets[`attr.${ENUM_COLUMN}`][0]
  expect(bucket.count).toBeLessThan(ofType.total)
  await page
    .getByRole("button", {
      name: t("table.filterBy", { column: enumColumn?.label as string }),
    })
    .click()
  await page.getByRole("checkbox", { name: new RegExp(bucket.label) }).check()
  await expect(page.getByText(totalText(bucket.count))).toBeVisible()
  await page.keyboard.press("Escape")

  // Tag missing: the UI total equals the API's for the same filter.
  const missing = await api<Page_<Resource>>(
    page,
    `/resources?pageSize=1&resourceType=${encodeURIComponent(TYPE)}&tagMissing=${TAG_KEY}&attr.${ENUM_COLUMN}=${bucket.value}`,
  )
  await sidebar
    .getByRole("radio", { name: t("resources.tags.missing") })
    .click()
  await sidebar.getByLabel(t("resources.tags.key")).fill(TAG_KEY)
  await sidebar.getByRole("button", { name: t("action.add") }).click()
  await expect(page.getByText(totalText(missing.total))).toBeVisible()

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

test("资源详情：基本信息、关联资源、刷新更新时间", async ({ page }) => {
  const list = await api<Page_<Resource>>(
    page,
    "/resources?pageSize=1&resourceType=AWS::SQS::Queue",
  )
  const r = list.items[0]
  const detail = await api<
    Resource & { arn: string; relationshipCount: number }
  >(page, `/resources/${r.id}`)
  const typeLabel = (
    await api<{ typeName: string; label: string }[]>(page, "/resource-types")
  ).find((x) => x.typeName === r.resourceType)?.label as string

  await page.goto(`resources/${r.id}`)
  await expect(
    page.getByRole("heading", { name: r.resourceName ?? r.resourceId }),
  ).toBeVisible()
  await expect(page.getByText(typeLabel).first()).toBeVisible()
  const basic = section(page, t("detail.basic"))
  await expect(basic.getByText(detail.arn)).toBeVisible()
  await expect(basic.getByText(r.awsRegion, { exact: true })).toBeVisible()

  // Relationship links, when the stack's data has any (a Config-sourced scan writes them).
  const related = await api<Page_<Resource & { relationshipCount: number }>>(
    page,
    "/resources?pageSize=200",
  )
  const connected = related.items.find((x) => x.relationshipCount > 0)
  const relations = section(page, t("detail.relationships"))
  if (connected) {
    await page.goto(`resources/${connected.id}`)
    await relations.getByRole("link").first().click()
    await expect(page).toHaveURL(/\/resources\/\d+/)
    await page.goto(`resources/${r.id}`)
  } else {
    await expect(relations.getByText(t("detail.noRelationships"))).toBeVisible()
  }

  // 刷新 re-reads the resource live; the "更新于" time moves forward.
  const before = await page
    .getByText(/^更新于/)
    .locator("time")
    .getAttribute("datetime")
  await page.getByRole("button", { name: t("action.refresh") }).click()
  await expect
    .poll(async () =>
      page
        .getByText(/^更新于/)
        .locator("time")
        .getAttribute("datetime"),
    )
    .not.toBe(before)
})

test("扫描：从表单发起单个账号的扫描，进入详情并成功，任务详情可打开", async ({
  page,
}) => {
  const accounts = await api<Page_<Account>>(page, "/accounts?pageSize=1")
  const account = accounts.items[0]

  await page.goto("scans")
  await page
    .getByRole("button", { name: t("action.scan"), exact: true })
    .click()
  const dialog = page.getByRole("dialog")
  await dialog
    .getByLabel(t("scans.form.accountsSearch"))
    .fill(account.accountId)
  await dialog.getByRole("checkbox", { name: account.accountName }).check()
  await dialog
    .getByRole("button", { name: t("action.scan"), exact: true })
    .click()

  await expect(page).toHaveURL(/\/scans\/\d+/)
  await expect(
    page.getByRole("heading", { name: new RegExp(t("scans.detail.title")) }),
  ).toBeVisible()
  await expect(page.getByText(t("scans.status.succeeded")).first()).toBeVisible(
    {
      timeout: 90_000,
    },
  )

  await page.getByRole("table").getByRole("link").first().click()
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

test("账号：详情显示区域 × 类型覆盖表", async ({ page }) => {
  const account = (await api<Page_<Account>>(page, "/accounts?pageSize=1"))
    .items[0]
  await page.goto(`accounts/${account.accountId}`)
  await expect(
    page.getByRole("heading", { name: account.accountName }),
  ).toBeVisible()
  const grid = section(page, t("accounts.matrix"))
  await expect(
    grid.getByRole("img", { name: t("accounts.cell.ok") }).first(),
  ).toBeVisible()
  await expect(grid.getByRole("columnheader").nth(1)).toBeVisible()
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
  await page.getByRole("link", { name: en("nav.overview") }).click()
  await expect(
    page.getByRole("heading", { name: en("nav.overview") }),
  ).toBeVisible()
})
