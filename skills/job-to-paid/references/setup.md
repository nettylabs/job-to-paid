# Setup: get the owner ready in about 5 minutes

Goal: a profile good enough to send a real quote today. Ask in small groups and use defaults. Show
an example of how numbers will look before saving anything country-specific.

Start with:

```bash
jtp doctor                      # where the data folder is, what's missing
jtp init --currency <CODE>      # only if not initialized yet (ISO code: USD, CAD, GBP, EUR, AUD, NZD, ZAR, ...)
jtp profile check               # what's still missing, in plain words
```

## The interview (one group at a time)

**1. The business** (needed for every document)
- Business name as customers should see it, plus legal name if different.
- The owner's name as it should appear when signing messages (`business.owner_name`, e.g. "Sam").
- Trade (used to suggest price book items and the forgotten-costs checklist).
- Phone, email, website, and the business address.
- Tax or business registration numbers that must appear on invoices (GST/HST no., VAT no.,
  ABN, EIN, USt-IdNr., and so on). Store each with its label, exactly as it should print.
- Logo: ask them to send it (PNG or JPG works everywhere; SVG only shows in the HTML version).
  Save it inside the data folder and set `business.logo_path` to that file. Ask for a brand colour
  or pick one from the logo.

```bash
jtp profile set business.name "Acme Plumbing & Gas"
jtp profile set business.address '["123 Main St", "Surrey, BC V3S 1A1"]'
jtp profile set business.registration_numbers '[{"label": "GST No.", "value": "123456789 RT0001"}]'
jtp profile set business.logo_path "/workspace/job-to-paid/logo.png"
jtp profile set business.brand_color "#0F766E"
```

**2. Money format and paperwork** (confirm with an example: "Your invoices will show 1,234.50 and dates
like 15 Oct 2026. Look right?")
- Currency (`locale.currency`). The symbol, separators and symbol position default by currency; override
  with `locale.currency_symbol`, `locale.symbol_position` (before/after), `locale.decimal_separator`,
  `locale.thousands_separator` if their country writes it differently.
- Currency code on totals (`locale.show_currency_code`): `auto` (the default) prints the code next to
  the totals when other currencies use the same symbol, e.g. "$1,234.50 CAD", so a customer can't mistake
  which dollar (or kr, or ¥) is meant. Set `always` or `never` if the owner prefers.
- Date style (`locale.date_format`): `D MMM YYYY` (default, unambiguous), `DD/MM/YYYY`, `MM/DD/YYYY`,
  `YYYY-MM-DD`, `DD.MM.YYYY`, `DD-MM-YYYY`.
- Paper size (`locale.paper_size`): `letter` (US, Canada, Mexico, Philippines) or `a4` (most others).
- Time zone (`locale.timezone`, e.g. `America/Vancouver`, `Europe/London`) so "today" and due dates are right.
- Language for documents (`locale.language`). If it isn't English, run `jtp profile labels`, translate
  **every** value (keep `{name}`, `{date}` and `{number}` exactly as they are), and save them with
  `jtp profile set labels '<json>'`. Also translate line
  descriptions you write. Labels are also how to match local paperwork rules, e.g. `"invoice": "Tax Invoice"`
  and `"credit_note": "Adjustment Note"` (Australia), or `"total_incl_tax": "Total (incl. GST)"`.
- The built-in PDF fonts cover English and most Western European languages. For other scripts
  (Polish, Greek, Cyrillic, Asian languages), PDFs are printed through a locally installed
  Chrome/Chromium/Edge. `jtp doctor` shows whether one was found (`browser_for_unicode_pdfs`).

**3. Taxes** (the one place to slow down)
- Ask: "Which taxes do you charge on your work, and at what rates? Do any apply only to materials
  or only to labour? Do your prices already include tax?" If they're unsure, you may look up the
  official rates for their location and *suggest* them with the source, but the owner decides.
- If they charge no tax (for example, below a registration threshold), save an empty list.
- Each tax: `code` (short), `name` (as printed), `rate` (percent), optional `applies_to` (any of
  labour, material, equipment, subcontract, fee, other; default all), optional `compound: true`
  (tax charged on top of the earlier taxes).
- Prices including tax (common for consumers in VAT countries): `tax.prices_include_tax true`.
- Ask: "When you tell me what a part cost you, is that before or after tax?" If they read costs off
  receipts *including* a tax they claim back (registered for GST/VAT), set `tax.costs_include_tax true`
  so the tax comes off before the markup. If they can't claim it back, the price paid is the real
  cost: leave it `false` (the default).
- Optional `tax.note`: a line printed on documents, e.g. a required exemption wording.
- Only after the owner confirms: `jtp profile set tax.confirmed true`. Quotes and invoices are
  blocked until then (previews with `--dry-run` still work).

```bash
jtp profile set tax.taxes '[{"code":"GST","name":"GST","rate":"5"},{"code":"PST","name":"PST","rate":"7","applies_to":["material","equipment"]}]'
jtp profile set tax.taxes '[{"code":"VAT","name":"VAT","rate":"20"}]'
jtp profile set tax.taxes '[]'
jtp profile set tax.confirmed true
```

Say plainly: "I'll apply exactly these. Your accountant or tax office has the final word."

**4. Pricing**
- Labour rates: a standard rate, plus any after-hours, emergency, apprentice or helper rates. Ask
  what each hour *costs them* (wage plus burden) for profit tracking; it's optional but valuable.
- Default markups on materials, equipment and subcontractors.
- Common items they quote a lot: add them to the price book now or let it fill up from quotes.

```bash
jtp profile set rates.labour '[{"key":"standard","name":"Labour","rate":"110","cost":"52"},{"key":"after_hours","name":"After-hours labour","rate":"165","cost":"60"}]'
jtp profile set rates.material_markup_pct 30
jtp pricebook add --name "40 gal gas water heater" --kind material --cost 980 --tags "water heater"
jtp pricebook import items.csv     # columns: name,kind,unit,cost,price,markup_pct,sku,supplier,tags
```

**5. Terms and getting paid**
- Quote valid for how many days (`terms.quote_valid_days`, default 30).
- Payment due how many days after the invoice (`terms.payment_due_days`; 0 means due on receipt).
- Deposit to book work (`terms.deposit_percent`, e.g. 25; 0 for none). If they only take deposits
  on bigger jobs ("25% on jobs over $2,500"), also set `terms.deposit_min_total` (e.g. 2500). Options
  below that total then show no deposit.
- Quote terms, invoice terms and warranty text (one or two plain sentences each).
- How customers pay: every method with exact instructions (bank details, e-transfer address,
  cheque payee, how to pay by card). Write `{number}` where the customer should quote a reference: it
  becomes the quote or invoice number on each document ("Use {number} as the reference"). Only mention a
  "Pay online" button if the owner will add payment links to invoices.

```bash
jtp profile set payment.methods '[{"name":"Interac e-Transfer","instructions":"Send to pay@acme.example (auto-deposit) with {number} in the message"},{"name":"Card","instructions":"Call 604 555 0100 to pay by card"}]'
jtp profile set terms.warranty "12-month workmanship warranty. Manufacturer warranties apply to equipment."
```

**6. Follow-ups** (show the defaults and ask if they're OK)
- Quote follow-ups: days after sending (`followups.quote_days`, default `[2, 7, 14]`).
- Payment reminders: days relative to the due date (`followups.invoice_days`, default
  `[-2, 1, 7, 14, 30]`; -2 means a friendly heads-up 2 days before it's due).
- Review requests after a job is paid (`followups.review_link` with their Google or other review
  link, `followups.review_request_days`, default 2). Empty link means off.

**7. Numbers**: if they already have invoices or quotes elsewhere, continue the sequence:

```bash
jtp numbering invoice 1047
jtp numbering quote 300
```

Prefixes can change too: `jtp profile set numbering.invoice_prefix "2026-"`.

Finish with `jtp profile check`. Then show the owner what customers will see: a preview works
before any customer exists and writes `previews/PREVIEW.pdf` (stamped DRAFT, nothing saved):

```bash
jtp quote create --dry-run --spec '{"title":"Sample job","lines":[{"kind":"labour","description":"Install","qty":3},{"kind":"material","description":"Parts","qty":1,"unit_cost":200}]}'
```

## Offer the routines

If the platform supports scheduled routines, offer these, in the owner's time zone. Create only the
ones they want.

1. **Morning money brief** (weekdays, around 7:30): "Run `jtp brief`. Send me the brief in under 10
   lines. Then, for each action in `actions`, draft the right message (job-to-paid
   references/messages.md) and show them to me together for approval. Don't send anything without my OK."
2. **End-of-day sweep** (weekdays, around 17:30): "Ask what was done today on which jobs. Log time
   and materials, mark finished jobs complete, and offer to invoice anything finished."
3. **Weekly review** (Friday afternoon): "Run `jtp report profit --from <7 days ago>`, `jtp report
   sales`, `jtp report tax` for this quarter, and `jtp backup`. Summarize in 6 lines with one
   suggestion to earn more."
4. **Payment watch** (optional, if email, Stripe, Square, Xero or QuickBooks is connected): "Check
   for new payments since yesterday, match them to open invoices, and ask me to confirm before
   recording each one."

## A dedicated back-office bot (optional)

If the owner uses several bots or a team, suggest a bot just for this, e.g. named "Office", with
this job description: "Back office for <business>: quotes, change orders, invoices, payment
follow-ups, morning money brief. Never sends anything to a customer or records a discount, credit or
void without the owner's approval." Teammates can then log time and materials there from the job site.

## Backups

Everything is plain files in the data folder. `jtp backup` makes a zip. If Google Drive or similar
is connected, offer to copy the latest backup there weekly.
