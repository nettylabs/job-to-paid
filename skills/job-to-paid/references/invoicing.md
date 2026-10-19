# Invoicing

## Pick the right invoice

| Situation | Command |
|---|---|
| Quoted job is done | `jtp invoice create --job J-0004`: the accepted option, plus approved change orders, plus logged extras, minus earlier progress invoices |
| Big job, bill in stages | `jtp invoice create --job J-0004 --progress 30` (repeat; the last one bills exactly what's left) |
| A deposit invoice (if the owner's tax rules need one) | `jtp invoice create --job J-0004 --deposit-invoice 25` |
| No quote, charge for time and materials | `jtp invoice create --job J-0007 --tm` |
| Quick one-off (service call) | `jtp invoice create --customer C-0003 --title "Service call: no hot water" --spec '[...]'` |
| Add something extra to any of the above | add `--spec '[{"kind":"fee","description":"Return visit","unit_price":89}]'` |
| Quote already fully billed, then a change order approved | `jtp invoice create --job J-0004` bills the approved change orders and extras |

**Adding up:** tax is worked out line by line, so an invoice for an accepted quote plus approved change
orders comes to exactly what the customer agreed to, and the last progress invoice bills exactly what's
left, tax included. Never adjust totals by hand.

**Estimates:** lines that were estimates on the quote stay marked "(estimate)" on the draft, also on
progress, deposit and balance invoices built from them and after `invoice update`, and `invoice issue`
refuses until the owner confirms them, returning the exact question to ask (`ask_owner`) and a one-time
code (`confirm`). Ask it word for word and wait. If a figure changed, fix the line with `invoice update`
and issue again: the old answer no longer counts, because the question names the figures. Only the
owner's yes when issuing clears an estimate. A line that repeats an estimate the owner hasn't confirmed
for the same customer (on another draft or change order, or one that was deleted or voided) is asked
about too, even without the mark: the output warns when you create or update such an invoice. If the
owner says yes, issue with `--owner-confirmed "<their exact words>" --confirm <code>`. Only a plain yes
as the whole answer counts ("yes", "yes, they're final"); "ok", "sure", an emoji or a yes with a
condition is refused (`unclear_answer`), so ask again, and a no stops it (`owner_declined`). Their reply
is kept on the invoice and in the activity log, so the owner can always see who confirmed what. Never
confirm on their behalf.


**Deposits, the simple way:** if the customer pays a deposit, record it on the job
(`jtp payment record --job J-0004 --deposit --amount 500 ...`). It comes off the next invoice
automatically when issued, shown as a payment received. Use a deposit *invoice* only when the owner
needs a formal invoice for the deposit itself (some tax systems do).

## The flow

1. **Preview**: add `--dry-run` and check the lines and total (it also writes `previews/PREVIEW.pdf`).
   The output shows `deposits_held` and `balance_after_deposits`, which is what the customer will
   actually owe. Tell the owner about any `warnings` (unapproved change orders, logged time not
   marked extra, estimates still to confirm).
2. **Create**: the same command without `--dry-run` makes a **draft** (its PDF says DRAFT).
3. **Owner check**: show the owner the total, the balance after deposits, and anything you decided
   (e.g. "I billed the flex connector as an extra"). Issuing locks the invoice, and after that,
   fixes need a credit note, so get their OK first. Fix a draft if needed:
   `jtp invoice show INV-0012 --as-spec > inv.json`, edit, then
   `jtp invoice update INV-0012 --spec inv.json`. Drafts that shouldn't exist:
   `jtp invoice delete INV-0012` works for the newest one; otherwise `jtp invoice void` (which asks
   the owner first).
4. **Issue** once the owner is happy: `jtp invoice issue INV-0012` sets the date and due date,
   locks it, applies held deposits, and makes the final PDF. Options: `--date`, `--due-days 0`
   (due on receipt), `--due-date 2026-11-30`. `--dry-run` shows the issued invoice (and the owner's
   question, if there is one) without issuing it. If it was already issued, it says so and changes
   nothing.
5. **Payment link** (if Stripe or Square is connected, see integrations.md): create a link for the
   balance, then `jtp invoice set INV-0012 --payment-link https://...`. The PDF gets a "Pay online"
   button.
6. **Send** with the owner's OK (messages.md, "Invoice"), attach the PDF, then
   `jtp invoice mark-sent INV-0012 --channel email --to jane@example.com`. A copy of the exact PDF
   sent is kept in `output/sent/`.
7. **Mirror to accounting** if the owner uses Xero or similar (integrations.md), and store its id:
   `jtp invoice set INV-0012 --external xero=INV-0457`.

## Corrections

Issued invoices are locked, which is how real books work.

- **Price wrong, goodwill discount, returned part**:
  `jtp credit create --invoice INV-0012 --amount 50 --reason "Goodwill: took longer than planned"`.
  The amount includes tax, and the toolkit splits it correctly across tax rates. Or pass exact lines
  with `--spec`. Preview first with `--dry-run`. Making it asks the owner first, the same way as
  estimates: the output has the question (`ask_owner`) and a code (`confirm`), and only their plain
  yes, sent back with `--owner-confirmed "<their exact words>" --confirm <code>`, makes the credit
  note. If the invoice was already paid, the output says a refund is due. Send the credit note to the
  customer like an invoice (messages.md, "Credit note"), then record it:
  `jtp credit mark-sent CN-0003 --channel email --to jane@example.com`. An invoice fully cancelled by
  a credit note shows as `credited`, not paid.
- **Whole invoice wrong** (wrong customer, duplicate): `jtp invoice void INV-0012 --reason "..."`. It
  can't be undone, so it asks the owner first, the same way. An issued invoice gets a full credit
  note. Anything billed on it (time, materials, change orders) becomes billable again. Invoices with
  payments can't be voided until the payments are moved (`jtp payment undo PAY-0009`) or refunded.

## Numbers and records

- Numbers run in order with no gaps, which many tax authorities require. Continue an existing
  sequence with `jtp numbering invoice 1047`.
- Everything is kept: voided invoices stay on record, credit notes are numbered, and
  `activity.jsonl` in the data folder logs every change.
