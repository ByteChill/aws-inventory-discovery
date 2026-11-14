import { execFileSync } from "node:child_process"
import { expect, test } from "@playwright/test"
import type { PageScanScopeOut } from "../src/client"
import { t } from "./i18n"

// Use the stack's actual scanned resources; never fabricate AWS responses. Only
// guard metadata changes, and the original values are restored in finally.
const engine =
  process.env.E2E_CONTAINER_ENGINE ?? (process.env.CI ? "docker" : "podman")
function guards(action: "prepare" | "restore", snapshot = ""): string {
  return execFileSync(
    engine,
    [
      "exec",
      "-i",
      "aws-inventory-discovery-api",
      "python",
      "-c",
      `
import json, sys
from sqlalchemy import text
from app.core.config import get_settings
from app.core.db import make_engine
settings = get_settings()
engine = make_engine(settings.DATABASE_URL, settings.DATABASE_SCHEMA)
with engine.begin() as conn:
    if sys.argv[1] == "prepare":
        rows = conn.execute(text("""
            SELECT s.id, s.guard_state, s.guard_detected_at,
                   s.candidate_count, s.candidate_ids
              FROM t_inv_aws_scan_shard s
             WHERE s.guard_state = 'normal' AND s.lease_token IS NULL
               AND EXISTS (SELECT 1 FROM t_inv_aws_resource r
                    WHERE r.account_id = s.account_id AND r.aws_region = s.region
                      AND r.resource_type = s.resource_type AND r.status = 'active')
             ORDER BY s.id LIMIT 201 FOR UPDATE
        """)).mappings().all()
        assert len(rows) == 201, "Run the full fixture-derived demo scan first"
        saved = [dict(row) for row in rows]
        conn.execute(text("""
            UPDATE t_inv_aws_scan_shard s
               SET guard_state = 'abnormal', guard_detected_at = now(),
                   candidate_count = (SELECT count(*) FROM t_inv_aws_resource r
                       WHERE r.account_id = s.account_id AND r.aws_region = s.region
                         AND r.resource_type = s.resource_type AND r.status = 'active'),
                   candidate_ids = (SELECT jsonb_agg(jsonb_build_object(
                       'resourceId', r.resource_id, 'arn', r.arn))
                       FROM t_inv_aws_resource r
                       WHERE r.account_id = s.account_id AND r.aws_region = s.region
                         AND r.resource_type = s.resource_type AND r.status = 'active')
             WHERE s.id = ANY(:ids)
        """), {"ids": [row["id"] for row in saved]})
        print(json.dumps(saved, default=str))
    else:
        for row in json.load(sys.stdin):
            if row["candidate_ids"] is not None:
                row["candidate_ids"] = json.dumps(row["candidate_ids"])
            conn.execute(text("""
                UPDATE t_inv_aws_scan_shard
                   SET guard_state = :guard_state,
                       guard_detected_at = :guard_detected_at,
                       candidate_count = :candidate_count,
                       candidate_ids = CAST(:candidate_ids AS jsonb)
                 WHERE id = :id
            """), row)
engine.dispose()
`,
      action,
    ],
    { input: snapshot, encoding: "utf8" },
  )
}

test("异常范围超过 200 个：翻页、直接链接、后续页失败重试和不存在状态", async ({
  page,
  context,
}) => {
  const snapshot = guards("prepare")
  try {
    const response = await page.request.get(
      "api/v1/scan-scopes?page=2&pageSize=200",
    )
    expect(response.ok()).toBeTruthy()
    const beyondFirstPage = (await response.json()) as PageScanScopeOut
    expect(beyondFirstPage.total).toBeGreaterThan(200)
    const target = beyondFirstPage.items[0]
    expect(target).toBeDefined()
    // The summary cache can predate the fixture; let its normal TTL expire.
    await expect
      .poll(
        async () => {
          const summary = await page.request.get("api/v1/scans/summary")
          return (await summary.json()).abnormalScopeCount
        },
        { timeout: 20_000 },
      )
      .toBeGreaterThan(200)

    await page.goto("scans")
    const block = page.locator("section").filter({
      has: page.getByRole("heading", { name: t("scans.anomalies"), level: 2 }),
    })
    for (let current = 1; current <= 4; current++) {
      await expect(
        block.getByRole("link", { name: t("scans.reviewResources") }),
      ).toHaveCount(50)
      await block.getByRole("button", { name: t("table.next") }).click()
      await expect(
        block.getByText(
          t("table.page", {
            page: current + 1,
            pages: Math.ceil(beyondFirstPage.total / 50),
          }),
          { exact: true },
        ),
      ).toBeVisible()
    }
    const review = block
      .getByRole("link", { name: t("scans.reviewResources") })
      .first()
    await expect(review).toHaveAttribute(
      "href",
      new RegExp(`/scan-scopes/${target.scanShardId}$`),
    )
    await review.click()
    await expect(page.getByText(t("scanScope.noteTitle"))).toBeVisible()
    await expect(
      page.getByText(target.accountId, { exact: true }).first(),
    ).toBeVisible()

    // A fresh tab has no query cache or previous list page. Fail the later page,
    // then retry against the real API: an incomplete lookup must not say missing.
    const direct = await context.newPage()
    let failLaterPage = true
    await direct.route(/\/api\/v1\/scan-scopes\?/, async (route) => {
      if (
        new URL(route.request().url()).searchParams.get("page") === "2" &&
        failLaterPage
      ) {
        await route.abort("failed")
      } else {
        await route.continue()
      }
    })
    await direct.goto(`scan-scopes/${target.scanShardId}`)
    await expect(direct.getByText(t("scanScope.notFoundPage"))).toHaveCount(0)
    await expect(
      direct.getByRole("button", { name: t("action.retry") }),
    ).toBeVisible({ timeout: 20_000 })
    failLaterPage = false
    await direct.getByRole("button", { name: t("action.retry") }).click()
    await expect(direct.getByText(t("scanScope.noteTitle"))).toBeVisible()
    const candidates = await direct.request.get(
      `api/v1/scan-scopes/${target.scanShardId}/candidates`,
    )
    expect(candidates.ok()).toBeTruthy()
    const first = (await candidates.json()).items[0]
    await expect(
      direct.getByRole("row", {
        name: t("preview.row", {
          name: first.resourceName ?? first.resourceId,
        }),
      }),
    ).toBeVisible()

    await direct.goto("scan-scopes/9007199254740991")
    await expect(direct.getByText(t("scanScope.notFoundPage"))).toBeVisible()
    await direct.close()
  } finally {
    guards("restore", snapshot)
  }
})
