import { expect, type Page, test } from "@playwright/test"
import { BASE_PATH } from "./base-url"
import { t } from "./i18n"

// The report is built from the stack's own inventory: a type that has an active
// resource, one of its type columns, and that resource's account. Every expected
// number comes from the API the page itself reads (reports.md: results are live).

type Page_<T> = { items: T[]; total: number }
type Facet = { value: string; label: string; count: number }
type Field = {
  key: string
  label: string
  type: string
  group: "common" | "type" | "relation"
  array: boolean
}
type Resource = {
  resourceId: string
  resourceName: string | null
  accountId: string
  status: string
}
type ReportPage = {
  items: Resource[]
  total: number
  columns: { key: string; label: string }[]
}
type ReportInfo = { id: number; name: string }

async function api<T>(page: Page, path: string): Promise<T> {
  const response = await page.request.get(`api/v1${path}`)
  expect(response.ok(), `${path}: ${response.status()}`).toBeTruthy()
  return (await response.json()) as T
}

/** A type with an active resource and a plain type column worth showing. */
async function pickSubject(page: Page) {
  const facets = await api<Record<string, Facet[]>>(
    page,
    "/resources/facets?field=resourceType",
  )
  for (const bucket of facets.resourceType ?? []) {
    if (!bucket.count) continue
    const fields = await api<Field[]>(
      page,
      `/reports/fields?subjectType=${encodeURIComponent(bucket.value)}`,
    )
    // The column picker locates checkboxes by label, so the label must be unique in the
    // palette (a type column may share its label with a common one, e.g. 名称).
    const field = fields.find(
      (item) =>
        item.group === "type" &&
        !item.array &&
        ["text", "enum", "number"].includes(item.type) &&
        fields.filter((other) => other.label === item.label).length === 1,
    )
    if (!field) continue
    const resources = await api<Page_<Resource>>(
      page,
      `/resources?pageSize=50&resourceType=${encodeURIComponent(bucket.value)}`,
    )
    const resource = resources.items.find((item) => item.status === "active")
    if (resource) return { type: bucket.value, field, resource }
  }
  throw new Error("没有同时具备运行中资源和普通类型字段的资源类型")
}

const count = (n: number) => n.toLocaleString("zh-CN")

test("报表：按账号新建规则、查看结果、导出、改名和删除", async ({ page }) => {
  const name = `E2E 报表 ${Date.now()}`
  const renamed = `${name}（改）`
  let reportId: number | undefined
  try {
    await page.goto("./")
    await page
      .getByRole("navigation", { name: t("nav.main") })
      .getByRole("link", { name: t("reports.title") })
      .click()
    await expect(
      page.getByRole("heading", { name: t("reports.title"), level: 1 }),
    ).toBeVisible()

    const subject = await pickSubject(page)

    await page.getByRole("button", { name: t("reports.create") }).click()
    const dialog = page.getByRole("dialog")
    // The column picker's checkboxes carry names that end in 名称 too; ask for the textbox.
    const nameInput = dialog.getByRole("textbox", {
      name: t("reports.name"),
      exact: true,
    })
    await nameInput.fill(name)
    await dialog.getByLabel(t("reports.subjectType")).selectOption(subject.type)

    // The palette loads after the type is chosen; then narrow to the account
    // that owns one real resource.
    const fields = dialog.getByLabel(t("reports.condition.field"))
    await expect(fields).toHaveCount(1)
    await dialog
      .getByRole("button", { name: t("reports.condition.addCondition") })
      .click()
    await fields.nth(1).selectOption("accountId")
    await dialog.getByLabel(t("reports.condition.op")).nth(1).selectOption("eq")
    await dialog
      .getByLabel(t("reports.condition.value"))
      .fill(subject.resource.accountId)

    // One type column makes the result table and the export carry it.
    await dialog
      .getByRole("checkbox", { name: subject.field.label, exact: true })
      .check()
    await dialog.getByRole("button", { name: t("reports.save") }).click()

    await expect(page).toHaveURL(/\/reports\/\d+/)
    reportId = Number(new URL(page.url()).pathname.split("/").pop())
    await expect(page.getByRole("heading", { name, level: 1 })).toBeVisible()

    const results = await api<ReportPage>(
      page,
      `/reports/${reportId}/results?pageSize=10`,
    )
    expect(results.total).toBeGreaterThan(0)
    for (const item of results.items)
      expect(item.accountId).toBe(subject.resource.accountId)
    const reportColumn = results.columns.find(
      (column) => column.key === subject.field.key,
    )
    expect(reportColumn).toBeDefined()
    await expect(
      page.getByRole("columnheader", { name: reportColumn?.label }),
    ).toBeVisible()
    await expect(
      page.getByText(
        t("table.range.resources", {
          from: 1,
          to: Math.min(10, results.total),
          count: results.total,
        }),
      ),
    ).toBeVisible()
    const firstRow = results.items[0]
    await expect(
      page
        .getByRole("row")
        .filter({ hasText: firstRow.resourceName ?? firstRow.resourceId })
        .first(),
    ).toBeVisible()

    // Export is a link carrying this report, the current language and no filters.
    await page.getByRole("button", { name: t("action.export") }).click()
    const href = await page
      .getByRole("menuitem", { name: t("action.exportCsv") })
      .getAttribute("href")
    const exported = new URL(href as string, page.url())
    expect(exported.pathname).toBe(
      `${BASE_PATH}/api/v1/reports/${reportId}/export`,
    )
    expect(exported.searchParams.get("format")).toBe("csv")
    expect(exported.searchParams.get("lang")).toBe("zh-CN")
    const csv = await page.request.get(exported.toString())
    expect(csv.ok()).toBeTruthy()
    expect(csv.headers()["content-type"]).toContain("text/csv")
    const lines = (await csv.text())
      .replace(/^﻿/, "")
      .split("\r\n")
      .filter(Boolean)
    expect(lines[0]).toContain(reportColumn?.label)
    expect(lines.length).toBe(results.total + 1)
    await page.keyboard.press("Escape")

    // Renaming re-reads the report in place.
    await page.getByRole("button", { name: t("reports.edit") }).click()
    const edit = page.getByRole("dialog")
    const editName = edit.getByRole("textbox", {
      name: t("reports.name"),
      exact: true,
    })
    await expect(editName).toHaveValue(name)
    await editName.fill(renamed)
    await edit.getByRole("button", { name: t("reports.save") }).click()
    await expect(edit).toHaveCount(0)
    await expect(
      page.getByRole("heading", { name: renamed, level: 1 }),
    ).toBeVisible()

    // Changing the global scope keeps the report open; it does not bounce to the overview.
    await page.getByRole("button", { name: t("scope.switch") }).click()
    const scopeDialog = page.getByRole("dialog")
    await scopeDialog
      .getByRole("searchbox", { name: t("scope.search") })
      .fill(subject.resource.accountId)
    await scopeDialog
      .getByRole("button", { name: new RegExp(subject.resource.accountId) })
      .click()
    await expect(page).toHaveURL(
      (url) =>
        url.pathname.endsWith(`/reports/${reportId}`) &&
        url.searchParams.get("s") === `acct:${subject.resource.accountId}`,
    )
    await expect(
      page.getByRole("heading", { name: renamed, level: 1 }),
    ).toBeVisible()

    // The list shows the matching count for each report.
    await page.goto("reports")
    const row = page.getByRole("link").filter({ hasText: renamed })
    await expect(row).toBeVisible()
    await expect(row).toContainText(count(results.total))

    await row.click()
    await expect(page).toHaveURL(new RegExp(`/reports/${reportId}$`))
    await page.getByRole("button", { name: t("reports.delete") }).click()
    const confirm = page.getByRole("dialog", {
      name: t("reports.deleteTitle", { name: renamed }),
    })
    await confirm.getByRole("button", { name: t("reports.delete") }).click()
    await expect(page).toHaveURL(/\/reports(\?|$)/)
    await expect(row).toHaveCount(0)
    const remaining = await api<ReportInfo[]>(page, "/reports")
    expect(remaining.some((report) => report.name === renamed)).toBe(false)
    reportId = undefined
  } finally {
    // A failed run must not leave the report behind for the next one.
    if (reportId !== undefined) {
      const response = await page.request.delete(`api/v1/reports/${reportId}`)
      expect([204, 404]).toContain(response.status())
    }
  }
})
