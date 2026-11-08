import { useMemo } from "react"

import type { Column } from "@/components/data-table"
import { useI18n } from "@/i18n"

function col(
  key: string,
  label: string,
  type: Column["type"],
  extra: Partial<Column> = {},
): Column {
  return {
    key,
    label,
    type,
    mono: false,
    sortable: true,
    filterable: false,
    scope: "common",
    ...extra,
  }
}

/**
 * Accounts and scans are small fixed tables, so their columns are defined here.
 * Resource columns come from GET /columns. Enum columns get a filter once the
 * API returns option counts for them.
 */
export function useAccountColumns(): Column[] {
  const { t } = useI18n()
  return useMemo(
    () => [
      col("accountName", t("accounts.column.name"), "text", {
        filterable: true,
      }),
      col("accountId", t("accounts.column.id"), "text", {
        mono: true,
        filterable: true,
      }),
      col("accountType", t("accounts.column.env"), "enum"),
      col("eimId", "EIM", "text", { filterable: true }),
      col("baId", "BA", "text", { filterable: true }),
      col("resourceCount", t("accounts.column.resources"), "number", {
        filterable: true,
      }),
      col("accessStatus", t("accounts.column.status"), "enum"),
      col("lastScanAt", t("accounts.column.lastScan"), "datetime", {
        filterable: true,
      }),
    ],
    [t],
  )
}

export function useScanColumns(): Column[] {
  const { t } = useI18n()
  return useMemo(
    () => [
      col("startedAt", t("scans.column.time"), "datetime", {
        filterable: true,
      }),
      col("trigger", t("scans.column.trigger"), "enum"),
      col("scope", t("scans.column.scope"), "text", { sortable: false }),
      col("status", t("scans.column.status"), "enum"),
      col("result", t("scans.column.result"), "text", { sortable: false }),
    ],
    [t],
  )
}
