# Command reference

Generated from `jtp <command> --help`. Run that for the same text at any time.

Global options (before the command): `--home <data folder>`, `--no-render` (skip automatic PDFs), `--version`.

Every command prints JSON (`brief --text` prints plain text). Errors print `{"ok": false, "error", "hint", "code"}` and exit with code 1 (2 for a mistyped command).

## init

```text
usage: jtp init [-h] [--currency CURRENCY]

Create the data folder and a blank business profile.

options:
  -h, --help           show this help message and exit
  --currency CURRENCY  ISO currency code, e.g. USD, CAD, GBP, EUR, AUD

Examples:
  jtp init --currency CAD
  jtp --home ~/my-business init --currency EUR
```

## doctor

```text
usage: jtp doctor [-h]

Check Python, the data folder, setup gaps and PDF options.

options:
  -h, --help  show this help message and exit

Examples:
  jtp doctor
```

## demo

```text
usage: jtp demo [-h] [--currency CURRENCY] [--no-history]

Fill a NEW folder with a demo business, customers, jobs, invoices and a quote.

options:
  -h, --help           show this help message and exit
  --currency CURRENCY
  --no-history         Only the business, customers and price book

Examples:
  jtp --home /tmp/jtp-demo demo
  jtp --home /tmp/jtp-demo demo --currency GBP --no-history
```

## selftest

```text
usage: jtp selftest [-h]

Run built-in checks (money math, a full job, PDFs) in a temporary folder.

options:
  -h, --help  show this help message and exit

Examples:
  jtp selftest
```

## profile

```text
usage: jtp profile [-h] [--section SECTION] {show,check,set,labels} [key] [value]

Show, check or change business settings.

positional arguments:
  {show,check,set,labels}
  key
  value

options:
  -h, --help            show this help message and exit
  --section SECTION

Examples:
  jtp profile check
  jtp profile show --section tax
  jtp profile labels   (document wording to translate)
  jtp profile set business.name "Acme Plumbing"
  jtp profile set tax.taxes '[{"code":"GST","name":"GST","rate":"5"}]'
  jtp profile set tax.confirmed true
  jtp profile set rates.labour '[{"key":"standard","name":"Labour","rate":"95","cost":"45"}]'
  jtp profile set payment.methods '[{"name":"Bank transfer","instructions":"Acct 123, ref invoice no."}]'
```

## numbering

```text
usage: jtp numbering [-h] {quote,invoice,change_order,credit_note,job,customer,payment} next

Set the next number (e.g. to continue from your old invoice numbers).

positional arguments:
  {quote,invoice,change_order,credit_note,job,customer,payment}
  next

options:
  -h, --help            show this help message and exit

Examples:
  jtp numbering invoice 1047
  jtp numbering quote 300
```

## customer

```text
usage: jtp customer [-h] [--name NAME] [--company COMPANY] [--email EMAIL] [--phone PHONE]
                    [--address ADDRESS] [--notes NOTES] [--language LANGUAGE] [--whatsapp WHATSAPP]
                    [--tax-id TAX_ID] [--site SITE] [--search SEARCH]
                    [--preferred {email,sms,whatsapp,phone,other}]
                    {add,list,show,update} [id]

Add, find, show or update customers.

positional arguments:
  {add,list,show,update}
  id

options:
  -h, --help            show this help message and exit
  --name NAME           Name
  --company COMPANY     Company name
  --email EMAIL         Email address
  --phone PHONE         Phone number
  --address ADDRESS     Address; separate lines with ; (e.g. '12 Maple Ave; Surrey BC V3S 1A1')
  --notes NOTES         Notes
  --language LANGUAGE   Language for this customer's documents and messages (e.g. en, fr, es)
  --whatsapp WHATSAPP   WhatsApp number if different from phone
  --tax-id TAX_ID       Customer's tax/VAT number (printed on their invoices)
  --site SITE           Job site address if different
  --search SEARCH       Words to match
  --preferred {email,sms,whatsapp,phone,other}

Examples:
  jtp customer add --name "Jane Doe" --phone "+1 604 555 0142" --email jane@example.com --address "12 Maple Ave; Surrey BC V3S 1A1"
  jtp customer list --search jane
  jtp customer show C-0001
  jtp customer update C-0001 --preferred whatsapp
```

## pricebook

```text
usage: jtp pricebook [-h] [--name NAME] [--kind KIND] [--unit UNIT] [--cost COST] [--price PRICE]
                     [--markup MARKUP] [--sku SKU] [--supplier SUPPLIER] [--tags TAGS] [--search SEARCH]
                     {add,list,remove,import} [id] [file]

Your saved items and prices (learned from your quotes).

positional arguments:
  {add,list,remove,import}
  id                    Item id (remove)
  file                  CSV with name,kind,unit,cost,price,markup_pct,sku,supplier (import)

options:
  -h, --help            show this help message and exit
  --name NAME           Name
  --kind KIND           labour, material, equipment, subcontract, fee or other
  --unit UNIT           Unit, e.g. ea, hr, m, ft, day
  --cost COST           What it costs you (your markup is added)
  --price PRICE         Fixed selling price
  --markup MARKUP       Markup percent (overrides your default)
  --sku SKU             Supplier part number
  --supplier SUPPLIER   Supplier name
  --tags TAGS           Job-type tags, comma separated (e.g. 'water heater,gas')
  --search SEARCH       Words to match

Examples:
  jtp pricebook add --name "1/2 in ball valve" --kind material --cost 18.50
  jtp pricebook add --name "Drain clearing (flat rate)" --kind labour --price 189
  jtp pricebook list --search valve
  jtp pricebook import items.csv
  jtp pricebook remove PB-0004
```

## job

```text
usage: jtp job [-h] [--customer CUSTOMER] [--title TITLE] [--site SITE] [--tags TAGS] [--notes NOTES]
               [--search SEARCH] [--status STATUS] [--completed COMPLETED] [--time TIME]
               [--material MATERIAL] [--expense EXPENSE] [--note NOTE] [--photo PHOTO] [--caption CAPTION]
               [--qty QTY] [--unit UNIT] [--cost COST] [--price PRICE] [--markup MARKUP] [--amount AMOUNT]
               [--rate RATE] [--who WHO] [--date DATE] [--receipt RECEIPT] [--supplier SUPPLIER]
               [--pricebook PRICEBOOK] [--note-text NOTE_TEXT] [--pricing {fixed,tm}]
               [--service-months SERVICE_MONTHS] [--extra] [--billable] [--not-billable]
               {create,list,show,update,log,unlog} [id] [entry]

Jobs and the work log (time, materials, expenses, notes, photos).

positional arguments:
  {create,list,show,update,log,unlog}
  id
  entry                 Log entry id (unlog)

options:
  -h, --help            show this help message and exit
  --customer CUSTOMER   Customer id, e.g. C-0001
  --title TITLE         Short title the customer sees
  --site SITE           Job site address if different
  --tags TAGS           Job-type tags, comma separated (e.g. 'water heater,gas')
  --notes NOTES         Notes
  --search SEARCH       Words to match
  --status STATUS       Filter by status
  --completed COMPLETED
                        Date the work was completed
  --time TIME           Hours worked
  --material MATERIAL   Material description
  --expense EXPENSE     Expense description (dump fee, parking...)
  --note NOTE           Free text kept on record
  --photo PHOTO         Path to a photo
  --caption CAPTION     Photo caption
  --qty QTY             Quantity
  --unit UNIT           Unit, e.g. ea, hr, m, ft, day
  --cost COST           What it costs you (your markup is added)
  --price PRICE         Fixed selling price
  --markup MARKUP       Markup percent (overrides your default)
  --amount AMOUNT       Amount (including tax where it's a payment or credit)
  --rate RATE           Labour rate key, e.g. standard or after_hours
  --who WHO             Who did the work
  --date DATE           YYYY-MM-DD, today, yesterday, or +N / -N days (default: today)
  --receipt RECEIPT     Path to the receipt photo or PDF
  --supplier SUPPLIER   Supplier name
  --pricebook PRICEBOOK
                        Price book item id or exact name
  --note-text NOTE_TEXT
                        A note to attach to a time/material/expense entry
  --pricing {fixed,tm}
  --service-months SERVICE_MONTHS
  --extra               Extra work outside a fixed-price quote: bill it
  --billable
  --not-billable

Examples:
  jtp job create --customer C-0001 --title "Replace water heater" --tags "water heater"
  jtp job list
  jtp job list --status complete
  jtp job show J-0001
  jtp job update J-0001 --status complete --service-months 12
  jtp job log J-0001 --time 3.5 --who Sam
  jtp job log J-0001 --material "Gas flex connector" --qty 1 --cost 28 --extra --receipt /path/r.jpg
  jtp job log J-0001 --expense "Dump fee" --amount 25
  jtp job log J-0001 --note "Customer wants a softener quote"
  jtp job unlog J-0001 L3
```

## quote

```text
usage: jtp quote [-h] [--job JOB] [--customer CUSTOMER] [--spec SPEC] [--title TITLE] [--channel CHANNEL]
                 [--to TO] [--option OPTION] [--by BY] [--method METHOD] [--note NOTE] [--reason REASON]
                 [--date DATE] [--until UNTIL] [--status STATUS] [--tags TAGS] [--days DAYS] [--dry-run]
                 [--as-spec]
                 {create,revise,show,list,mark-sent,accept,decline,extend,expire} [id]

Create, revise, send-track, accept or decline quotes.

Spec format (JSON):
  {"title": "Water heater replacement",
   "intro": "Thanks for having us out...",            (optional)
   "lines": [                                          (one option)
     {"kind": "labour", "description": "Install and test", "qty": 4},          uses your labour rate
     {"kind": "labour", "rate_key": "after_hours", "description": "Call-out", "qty": 1},
     {"kind": "material", "description": "40 gal heater", "qty": 1, "unit_cost": 980},  cost + your markup
     {"kind": "material", "pricebook": "PB-0003", "qty": 2},                   from your price book
     {"kind": "fee", "description": "Permit", "unit_price": 175},
     {"kind": "discount", "description": "Repeat customer", "percent": 10},
     {"kind": "text", "description": "A wording-only line (no price)"}],
   OR "options": [{"name": "Good", "lines": [...]}, {"name": "Better", "recommended": true, "lines": [...]}],
   "deposit_percent": 30,  or  "deposit_amount": 500,   (optional, default from your profile)
   "valid_days": 30, "notes": "...", "terms": "..."}   (optional)
unit_cost is what the owner pays (markup is added); a bare figure from the owner is a cost.
Add "estimate": true to a line whose price still needs confirming.
Add "tax_codes": [] (no tax) or ["GST"] only when the owner says that line is taxed differently.
--dry-run prices it without saving and writes output/PREVIEW.pdf to show the owner.

positional arguments:
  {create,revise,show,list,mark-sent,accept,decline,extend,expire}
  id

options:
  -h, --help            show this help message and exit
  --job JOB             Job id, e.g. J-0001
  --customer CUSTOMER   Customer id, e.g. C-0001
  --spec SPEC           Lines as JSON: a file path, - for stdin, or inline JSON
  --title TITLE         Short title the customer sees
  --channel CHANNEL     email, sms, whatsapp, phone, in_person, portal or other
  --to TO               Where it went (email address or phone number)
  --option OPTION       The option the customer chose (A, B, C...)
  --by BY               Who accepted or approved (customer name)
  --method METHOD       How: email, text message, signed PDF, in person... (payments: e-Transfer, card,
                        cash...)
  --note NOTE           Free text kept on record
  --reason REASON       Why (kept on record)
  --date DATE           YYYY-MM-DD, today, yesterday, or +N / -N days (default: today)
  --until UNTIL         A date: YYYY-MM-DD or +N days
  --status STATUS       Filter by status
  --tags TAGS           Job-type tags, comma separated (e.g. 'water heater,gas')
  --days DAYS
  --dry-run             Price it without saving (writes output/PREVIEW.pdf)
  --as-spec             Show as an editable spec

Examples:
  jtp quote create --customer C-0001 --spec quote.json --dry-run   (totals + output/PREVIEW.pdf)
  jtp quote create --spec quote.json --dry-run   (a sample PDF before any customer exists)
  jtp quote create --job J-0002 --spec quote.json
  jtp quote mark-sent Q-0001 --channel email --to jane@example.com
  jtp quote accept Q-0001 --option B --by "Jane Doe" --method email
  jtp quote revise Q-0001 --spec quote-v2.json
  jtp quote show Q-0001 --as-spec
  jtp quote extend Q-0001 --days 14
  jtp quote decline Q-0001 --reason 'Went with cheaper'
```

## co

```text
usage: jtp co [-h] [--job JOB] [--spec SPEC] [--channel CHANNEL] [--to TO] [--by BY] [--method METHOD]
              [--note NOTE] [--reason REASON] [--date DATE] [--dry-run]
              {create,update,show,list,mark-sent,approve,decline,void} [id]

Change orders: get extra or changed work approved before doing it.

positional arguments:
  {create,update,show,list,mark-sent,approve,decline,void}
  id

options:
  -h, --help            show this help message and exit
  --job JOB             Job id, e.g. J-0001
  --spec SPEC           Lines as JSON: a file path, - for stdin, or inline JSON
  --channel CHANNEL     email, sms, whatsapp, phone, in_person, portal or other
  --to TO               Where it went (email address or phone number)
  --by BY               Who accepted or approved (customer name)
  --method METHOD       How: email, text message, signed PDF, in person... (payments: e-Transfer, card,
                        cash...)
  --note NOTE           Free text kept on record
  --reason REASON       Why (kept on record)
  --date DATE           YYYY-MM-DD, today, yesterday, or +N / -N days (default: today)
  --dry-run             Preview it (output/PREVIEW.pdf) without saving

Examples:
  jtp co create --job J-0001 --spec '{"title":"Add shut-off valve","reason":"Old valve seized","lines":[{"kind":"material","pricebook":"PB-0005","qty":1},{"kind":"labour","description":"Swap valve","qty":0.5}]}'
  jtp co approve CO-0001 --by "Jane Doe" --method "text message"
  jtp co list --job J-0001
```

## invoice

```text
usage: jtp invoice [-h] [--job JOB] [--customer CUSTOMER] [--spec SPEC] [--title TITLE] [--channel CHANNEL]
                   [--to TO] [--date DATE] [--due-date DUE_DATE] [--reason REASON]
                   [--payment-link PAYMENT_LINK] [--notes NOTES] [--status STATUS] [--tags TAGS]
                   [--from-quote [FROM_QUOTE]] [--progress PROGRESS] [--deposit-invoice DEPOSIT_INVOICE]
                   [--tm] [--no-change-orders] [--no-extras] [--due-days DUE_DAYS] [--external EXTERNAL]
                   [--allow-unconfirmed-tax] [--dry-run] [--as-spec]
                   {create,update,issue,show,list,mark-sent,set,void,delete} [id]

Create, issue, track and correct invoices.

positional arguments:
  {create,update,issue,show,list,mark-sent,set,void,delete}
  id

options:
  -h, --help            show this help message and exit
  --job JOB             Job id, e.g. J-0001
  --customer CUSTOMER   Customer id, e.g. C-0001
  --spec SPEC           Lines as JSON: a file path, - for stdin, or inline JSON
  --title TITLE         Short title the customer sees
  --channel CHANNEL     email, sms, whatsapp, phone, in_person, portal or other
  --to TO               Where it went (email address or phone number)
  --date DATE           YYYY-MM-DD, today, yesterday, or +N / -N days (default: today)
  --due-date DUE_DATE   Explicit due date
  --reason REASON       Why (kept on record)
  --payment-link PAYMENT_LINK
                        Online payment URL (https://...)
  --notes NOTES         Notes
  --status STATUS       Filter by status
  --tags TAGS           Job-type tags, comma separated (e.g. 'water heater,gas')
  --from-quote [FROM_QUOTE]
                        Bill the accepted quote (optional id)
  --progress PROGRESS   Bill this percent of the accepted quote
  --deposit-invoice DEPOSIT_INVOICE
                        A deposit invoice for this percent of the accepted quote
  --tm                  Bill unbilled time, materials and expenses from the log
  --no-change-orders
  --no-extras
  --due-days DUE_DAYS
  --external EXTERNAL   key=value id in another system, e.g. xero=INV-88
  --allow-unconfirmed-tax
  --dry-run             Preview it (output/PREVIEW.pdf) without saving
  --as-spec

Examples:
  jtp invoice create --job J-0001            (accepted quote + approved change orders + extras)
  jtp invoice create --job J-0001 --progress 30   (30% progress invoice)
  jtp invoice create --job J-0001 --deposit-invoice 25
  jtp invoice create --job J-0002 --tm            (time and materials from the log)
  jtp invoice create --customer C-0003 --spec '[{"kind":"fee","description":"Service call","unit_price":189}]'
  jtp invoice issue INV-0001
  jtp invoice mark-sent INV-0001 --channel email --to jane@example.com
  jtp invoice set INV-0001 --payment-link https://buy.stripe.com/...
  jtp invoice list --status overdue
  jtp invoice void INV-0001 --reason 'Billed wrong customer'
```

## credit

```text
usage: jtp credit [-h] [--invoice INVOICE] [--amount AMOUNT] [--reason REASON] [--spec SPEC]
                  [--channel CHANNEL] [--to TO] [--date DATE] [--dry-run]
                  {create,show,mark-sent} [id]

Credit notes: the proper way to correct an issued invoice.

positional arguments:
  {create,show,mark-sent}
  id

options:
  -h, --help            show this help message and exit
  --invoice INVOICE     Invoice id, e.g. INV-0001
  --amount AMOUNT       Amount (including tax where it's a payment or credit)
  --reason REASON       Why (kept on record)
  --spec SPEC           Lines as JSON: a file path, - for stdin, or inline JSON
  --channel CHANNEL     email, sms, whatsapp, phone, in_person, portal or other
  --to TO               Where it went (email address or phone number)
  --date DATE           YYYY-MM-DD, today, yesterday, or +N / -N days (default: today)
  --dry-run             Preview it (output/PREVIEW.pdf) without saving

Examples:
  jtp credit create --invoice INV-0003 --amount 50 --reason "Goodwill discount"
  jtp credit create --invoice INV-0003 --spec credit.json
  jtp credit mark-sent CN-0001 --channel email --to jane@example.com
  jtp credit show CN-0001
```

## payment

```text
usage: jtp payment [-h] [--job JOB] [--amount AMOUNT] [--method METHOD] [--date DATE]
                   [--reference REFERENCE] [--note NOTE] [--from START] [--to END] [--deposit]
                   [--allow-duplicate] [--format {pdf,html,both}] [--engine {auto,builtin,chrome}]
                   {record,list,undo,receipt} [ID]

Record payments and deposits, list them, undo mistakes, make receipts.

positional arguments:
  {record,list,undo,receipt}
  ID

options:
  -h, --help            show this help message and exit
  --job JOB             Job id, e.g. J-0001
  --amount AMOUNT       Amount (including tax where it's a payment or credit)
  --method METHOD       How: email, text message, signed PDF, in person... (payments: e-Transfer, card,
                        cash...)
  --date DATE           YYYY-MM-DD, today, yesterday, or +N / -N days (default: today)
  --reference REFERENCE
                        Payment reference (e-transfer id, cheque no.)
  --note NOTE           Free text kept on record
  --from START
  --to END
  --deposit
  --allow-duplicate
  --format {pdf,html,both}
  --engine {auto,builtin,chrome}

Examples:
  jtp payment record INV-0003 --amount 1543.50 --method "e-Transfer" --reference CA1234
  jtp payment record --job J-0004 --deposit --amount 500 --method "Bank transfer"
  jtp payment list --from 2026-10-01
  jtp payment undo PAY-0007
  jtp payment receipt PAY-0007
```

## render

```text
usage: jtp render [-h] [--format {pdf,html,both}] [--engine {auto,builtin,chrome}] [--out OUT] id

Make (or remake) the PDF/HTML for a document.

positional arguments:
  id

options:
  -h, --help            show this help message and exit
  --format {pdf,html,both}
  --engine {auto,builtin,chrome}
  --out OUT             Folder (default: <data folder>/output)

Examples:
  jtp render Q-0001
  jtp render INV-0004 --format both
  jtp render INV-0004 --engine chrome
```

## price

```text
usage: jtp price [-h] --spec SPEC

Price some lines without saving anything.

options:
  -h, --help   show this help message and exit
  --spec SPEC

Examples:
  jtp price --spec '[{"kind":"labour","description":"Install","qty":3},{"kind":"material","description":"Valve","qty":2,"unit_cost":18.5}]'
```

## followups

```text
usage: jtp followups [-h] [--stage STAGE] [--channel CHANNEL] [--note NOTE] [--date DATE] [--until UNTIL]
                     {due,done,skip,snooze} [id]

What to chase today, and recording what was sent.

positional arguments:
  {due,done,skip,snooze}
  id

options:
  -h, --help            show this help message and exit
  --stage STAGE         Stage from `jtp followups due` (a number, review, service:<date> or approval)
  --channel CHANNEL     email, sms, whatsapp, phone, in_person, portal or other
  --note NOTE           Free text kept on record
  --date DATE           YYYY-MM-DD, today, yesterday, or +N / -N days (default: today)
  --until UNTIL         A date: YYYY-MM-DD or +N days

Examples:
  jtp followups due
  jtp followups done INV-0003 --stage 2 --channel email
  jtp followups skip Q-0007 --stage 1 --note 'Talked on the phone'
  jtp followups done J-0004 --stage review --channel sms
  jtp followups snooze INV-0003 --until 2026-10-24 --note 'Promised to pay Friday'
```

## brief

```text
usage: jtp brief [-h] [--date DATE] [--text]

The money brief: owed, overdue, quotes waiting, unbilled work, today's follow-ups.

options:
  -h, --help   show this help message and exit
  --date DATE
  --text       Plain text only

Examples:
  jtp brief
  jtp brief --text
```

## report

```text
usage: jtp report [-h] [--from START] [--to END] [--tag TAG] [--basis {accrual,cash}]
                  {aging,profit,pricing,tax,sales}

Reports: aging, profit, pricing, tax, sales.

positional arguments:
  {aging,profit,pricing,tax,sales}

options:
  -h, --help            show this help message and exit
  --from START
  --to END
  --tag TAG
  --basis {accrual,cash}
                        tax: accrual = invoices issued in the period (default); cash = payments received in
                        the period

Examples:
  jtp report aging
  jtp report profit --from 2026-07-01 --to 2026-09-30
  jtp report pricing --tag "water heater"
  jtp report tax --from 2026-07-01 --to 2026-09-30
  jtp report tax --from 2026-07-01 --to 2026-09-30 --basis cash
  jtp report sales --from 2026-01-01
```

## export

```text
usage: jtp export [-h] [--from START] [--to END] [--out OUT]
                  {invoices,lines,payments,customers,expenses,quotes}

CSV exports for your accountant or accounting app.

positional arguments:
  {invoices,lines,payments,customers,expenses,quotes}

options:
  -h, --help            show this help message and exit
  --from START
  --to END
  --out OUT

Examples:
  jtp export invoices --from 2026-07-01 --to 2026-09-30
  jtp export payments
  jtp export expenses --out ~/expenses.csv
```

## search

```text
usage: jtp search [-h] words [words ...]

Find customers, jobs and documents by any words.

positional arguments:
  words

options:
  -h, --help  show this help message and exit

Examples:
  jtp search jane water heater
  jtp search 555 0142
```

## backup

```text
usage: jtp backup [-h] [--out OUT]

Zip the whole data folder.

options:
  -h, --help  show this help message and exit
  --out OUT

Examples:
  jtp backup
  jtp backup --out ~/jtp-backup.zip
```
