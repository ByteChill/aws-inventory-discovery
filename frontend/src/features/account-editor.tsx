import { type FormEvent, type ReactNode, useEffect, useState } from "react"

import { useCreateAccount, useUpdateAccount } from "@/api/accounts"
import type { AccountDetail, AccountSummary } from "@/client"
import { InlineError } from "@/components/inline-state"
import { Button } from "@/components/ui/button"
import { Dialog } from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { type MessageKey, useI18n } from "@/i18n"
import { errorText } from "@/lib/errors"

type EditableAccount = AccountSummary | AccountDetail
type AccountType = "dev" | "preprod" | "prod"

const TYPES: AccountType[] = ["dev", "preprod", "prod"]

export function AccountEditor({
  open,
  account,
  onClose,
  onSaved,
}: {
  open: boolean
  /** Omitted when creating an account. */
  account?: EditableAccount
  onClose: () => void
  onSaved?: (account: AccountDetail) => void
}) {
  const { t } = useI18n()
  const create = useCreateAccount()
  const update = useUpdateAccount()
  const [accountId, setAccountId] = useState("")
  const [accountName, setAccountName] = useState("")
  const [accountType, setAccountType] = useState<AccountType>("prod")
  const [eimId, setEimId] = useState("")
  const [eimApp, setEimApp] = useState("")
  const [baId, setBaId] = useState("")
  const error = create.error ?? update.error
  const saving = create.isPending || update.isPending

  // Open a fresh form for the chosen account.
  // biome-ignore lint/correctness/useExhaustiveDependencies: reset only when the dialog opens
  useEffect(() => {
    if (!open) return
    create.reset()
    update.reset()
    setAccountId(account?.accountId ?? "")
    setAccountName(account?.accountName ?? "")
    setAccountType(account?.accountType ?? "prod")
    setEimId(account?.eimId ?? "")
    setEimApp(account?.eimApp ?? "")
    setBaId(account?.baId ?? "")
  }, [open, account?.accountId])

  function submit(event: FormEvent) {
    event.preventDefault()
    const name = accountName.trim()
    if (!name) return
    if (!account && !/^\d{12}$/.test(accountId.trim())) return
    const basic = {
      accountName: name,
      accountType,
      eimId: eimId.trim() || null,
      eimApp: eimApp.trim() || null,
      baId: baId.trim() || null,
    }
    const done = (saved: AccountDetail) => {
      onSaved?.(saved)
      onClose()
    }
    if (account) {
      update.mutate(
        { accountId: account.accountId, body: basic },
        { onSuccess: done },
      )
    } else {
      create.mutate(
        { accountId: accountId.trim(), ...basic },
        { onSuccess: done },
      )
    }
  }

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title={t(account ? "accounts.editTitle" : "accounts.createTitle")}
    >
      <form onSubmit={submit} className="flex flex-col gap-4">
        <Field label={t("accounts.column.id")}>
          <Input
            value={accountId}
            onChange={(e) => setAccountId(e.target.value)}
            readOnly={account !== undefined}
            inputMode="numeric"
            pattern={"\\d{12}"}
            required
            aria-label={t("accounts.column.id")}
            className="font-mono"
          />
        </Field>
        <Field label={t("accounts.column.name")}>
          <Input
            value={accountName}
            onChange={(e) => setAccountName(e.target.value)}
            required
            aria-label={t("accounts.column.name")}
          />
        </Field>
        <Field label={t("accounts.column.env")}>
          <select
            value={accountType}
            onChange={(e) => setAccountType(e.target.value as AccountType)}
            aria-label={t("accounts.column.env")}
            className="h-8 w-full rounded-md border border-input bg-card px-2.5 text-foreground"
          >
            {TYPES.map((value) => (
              <option key={value} value={value}>
                {t(`env.${value}` as MessageKey)}
              </option>
            ))}
          </select>
        </Field>
        <div className="grid gap-4 sm:grid-cols-2">
          <Field label="EIM">
            <Input
              value={eimId}
              onChange={(e) => setEimId(e.target.value)}
              inputMode="numeric"
              pattern={"\\d{8}"}
              placeholder={t("accounts.optional")}
              aria-label="EIM"
              className="font-mono"
            />
          </Field>
          <Field label={t("accounts.eimApp")}>
            <Input
              value={eimApp}
              onChange={(e) => setEimApp(e.target.value)}
              placeholder={t("accounts.optional")}
              aria-label={t("accounts.eimApp")}
            />
          </Field>
        </div>
        <Field label="BA">
          <Input
            value={baId}
            onChange={(e) => setBaId(e.target.value)}
            placeholder={t("accounts.optional")}
            aria-label="BA"
            className="font-mono"
          />
        </Field>
        {!account && (
          <p className="text-muted-foreground">
            {t("accounts.createPolicyHint")}
          </p>
        )}
        {error && <InlineError message={errorText(t, error)} />}
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>
            {t("action.cancel")}
          </Button>
          <Button type="submit" disabled={saving || !accountName.trim()}>
            {t("action.save")}
          </Button>
        </div>
      </form>
    </Dialog>
  )
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1.5">
      <span className="text-xs font-semibold text-muted-foreground">
        {label}
      </span>
      {children}
    </div>
  )
}
