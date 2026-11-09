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
 * Resource columns come from GET /columns. The accounts API filters by
 * environment, EIM, BA and status; name and ID are searched with `q`.
 */
export function useAccountColumns(): Column[] {
  const { t } = useI18n()
  return useMemo(
    () => [
      col("accountName", t("accounts.column.name"), "text"),
      col("accountId", t("accounts.column.id"), "text", { mono: true }),
      col("accountType", t("accounts.column.env"), "enum", {
        filterable: true,
      }),
      col("eimId", "EIM", "text", { filterable: true }),
      col("baId", "BA", "text", { filterable: true }),
      col("resourceCount", t("accounts.column.resources"), "number"),
      col("accessStatus", t("accounts.column.status"), "enum", {
        filterable: true,
      }),
      col("lastScanOkAt", t("accounts.column.lastScan"), "datetime"),
    ],
    [t],
  )
}

export function useScanColumns(): Column[] {
  const { t } = useI18n()
  return useMemo(
    () => [
      col("createdAt", t("scans.column.time"), "datetime", {
        sortable: false,
      }),
      col("trigger", t("scans.column.trigger"), "enum", {
        sortable: false,
        filterable: true,
      }),
      col("scope", t("scans.column.scope"), "text", { sortable: false }),
      col("status", t("scans.column.status"), "enum", {
        sortable: false,
        filterable: true,
      }),
      col("result", t("scans.column.result"), "text", { sortable: false }),
    ],
    [t],
  )
}

export function useTaskColumns(): Column[] {
  const { t } = useI18n()
  return useMemo(
    () => [
      col("id", "ID", "text", { mono: true, sortable: false }),
      col("kind", t("tasks.column.kind"), "enum", {
        sortable: false,
        filterable: true,
      }),
      col("accountId", t("tasks.column.account"), "text", {
        mono: true,
        sortable: false,
      }),
      col("region", t("tasks.column.region"), "text", {
        mono: true,
        sortable: false,
      }),
      col("resourceType", t("tasks.column.resource"), "text", {
        sortable: false,
      }),
      col("status", t("tasks.column.status"), "enum", {
        sortable: false,
        filterable: true,
      }),
      col("attempt", t("tasks.column.attempt"), "text", { sortable: false }),
      col("errorCode", t("tasks.column.reason"), "enum", {
        sortable: false,
        filterable: true,
      }),
      col("finishedAt", t("tasks.column.finished"), "datetime", {
        sortable: false,
      }),
    ],
    [t],
  )
}
