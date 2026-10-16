# Jobs, the work log, and change orders

## Jobs

Every quote and invoice belongs to a job. Quotes and invoices created with `--customer` make the
job automatically. To make one by hand:

```bash
jtp job create --customer C-0001 --title "Replace water heater" --site "88 Cedar Rd" --tags "water heater"
jtp job list                       # open jobs
jtp job list --status complete     # finished, maybe not billed
jtp job show J-0004                # everything: quote, change orders, invoices, money, hours vs quote
jtp job update J-0004 --status scheduled
```

Statuses: `lead`, `quoted`, `won`, `scheduled`, `in_progress`, `complete`, `invoiced`, `paid`,
`closed`, `lost`, `cancelled`. Most move by themselves (sending, accepting, logging, invoicing,
payment). Set `scheduled` and `complete` yourself when the owner says so. When a job is
complete, offer to invoice it straight away.

**Recurring work** (annual servicing, filter changes, backflow tests):
`jtp job update J-0004 --service-months 12`. A service reminder comes up 14 days before it's due.

## The work log

Log what happened, as it happens, from the owner or a teammate:

```bash
jtp job log J-0004 --time 3.5 --who Sam                     # hours at the standard rate
jtp job log J-0004 --time 2 --rate after_hours --who Sam
jtp job log J-0004 --material "Gas flex connector" --qty 1 --cost 28 --receipt /path/receipt.jpg
jtp job log J-0004 --material "Valve" --pricebook PB-0005 --qty 2
jtp job log J-0004 --expense "Dump fee" --amount 25
jtp job log J-0004 --note "Customer asked about a water softener"
jtp job log J-0004 --photo /path/before.jpg --caption "Old tank, leaking at the base"
jtp job unlog J-0004 L7                                     # remove a mistake (if not billed yet)
```

**How billing works for log entries:**

- **Time and materials jobs** (no accepted quote): everything logged is billable by default and
  goes on the next `jtp invoice create --tm`.
- **Fixed-price jobs** (accepted quote): logged time and materials are for **job costing only**
  (so you learn the real profit) unless marked `--extra`. Extras are billed on the final invoice
  under "Additional work". Use `--extra` only when the customer agreed to pay for it, ideally
  through a change order first.

Receipts: when the owner sends a receipt photo, read the supplier, items and total. Ask which job
it's for if it isn't obvious, log it with `--receipt <saved file>`, and say whether it's billable.

Teammates in a shared chat can log time, materials, notes and photos. Only the owner approves
anything that goes to a customer or changes money (discounts, credit notes, voids).

## Change orders

Use a change order whenever the agreed scope changes: hidden problems, customer add-ons, upgraded
materials, extra visits. Getting it approved **before** doing the work is what stops "I never
agreed to that."

1. Capture what changed, why, and the price, using the same line format as quotes. A swap (one
   thing out, another in) can include a `discount` line for the removed part. If work is only being
   *removed*, don't make a change order. Note it on the job, and take it off the final invoice
   with a discount that applies to the quoted lines:
   `--spec '[{"kind":"discount","amount":150,"description":"Removed: towel rail","section":null}]'`.
2. Preview, then create:

```bash
jtp co create --job J-0004 --spec '{"title": "Replace seized gas shut-off valve",
  "reason": "Existing valve is seized and leaking at the stem",
  "lines": [{"kind": "material", "description": "1/2 in gas ball valve", "qty": 1, "unit_cost": 24},
            {"kind": "labour", "description": "Replace valve and leak test", "qty": 0.75}]}' --dry-run
```

3. Send it for approval (messages.md, "Change order"). On site, a quick text is often best: "Found
   X. Fixing it adds {total incl. tax}. Reply YES to go ahead." Then `jtp co mark-sent CO-0001
   --channel sms --to "+1..."`.
4. Record the approval exactly as it came:
   `jtp co approve CO-0001 --by "Jane Doe" --method "text message" --note "Replied YES 2:14pm"`.
   If they decline: `jtp co decline CO-0001 --reason "..."`.

Approved change orders are added to the final invoice automatically, each in its own section.
Unapproved ones are left off, with a warning, so nothing gets billed without a yes.
