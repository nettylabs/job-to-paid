# Changelog

## 1.0.5 (2026-10-19)

Follow-ups from independent rechecks of 1.0.4, and from checks of early builds of 1.0.5.

- **Invoices and quotes use the computer's date.** Only the estimate checks keep a date that never goes backwards.
  Before, a computer clock that was ahead even once, or a time zone switched and back late in the day, dated every
  later quote, invoice and due date ahead until the real date caught up.
- **The estimate checks' dates only move the way that asks more.** An estimate that leaves its invoice is dated no
  earlier than the latest date the data folder was used on, and nothing ever moves a recorded date; its 30 days
  count from the computer's date. The owner's yes counts for 30 days from when it's given, never while it's dated
  later than the computer's date, and never once it's 30 days older than the latest date used. So no clock setting
  can make an estimate lapse early or a yes last longer, and nothing needs resetting: after a clock that was ahead,
  the questions may just come more often until the real date catches up. `jtp clock show` shows the dates, and
  `jtp doctor` says when the computer's date is behind.
- **Time zone names are checked.** `jtp profile set locale.timezone` refuses a name that isn't in the time zone
  database ("Mars/Olympus") or isn't a place ("localtime", "Factory"), and spells one the database's way
  ("europe/london" is Europe/London). `profile check` and `doctor` report an unknown one saved earlier, and an
  invoice isn't issued until it's fixed. Before, it was accepted, and "today" quietly used the computer's local
  time.
- **SECURITY.md** says that copies within the amount the owner said yes to aren't asked about again, however many
  there are, and that yeses on separate invoices aren't added together.
- 114 automated tests on Python 3.9, 3.11 and 3.13, and a 50-check self-test.

## 1.0.4 (2026-10-19)

Fixes from independent checks of 1.0.3 and of an early build of 1.0.4.

- **Removed estimates ask once per amount, for 30 days.** An estimate that left its invoice (changed or taken off a
  draft, deleted or voided) is still asked about on another invoice with its description. The owner's yes to such
  a line confirms the estimate up to that line's amount: a copy up to it isn't asked about again, a bigger one is,
  and a yes that reaches the estimate's own figure clears it. So a yes to a $1 copy doesn't clear an $89 estimate.
  Before, a deleted "Labour 8 h" made the next several "Labour 2 h" invoices ask, and "Extra labour" or "Spare
  parts" asked months later.
- **The 30 days can't be moved.** They're counted on the computer's clock, and the date never goes backwards: the
  data folder keeps the latest date it was used on (`clock.json`), so a removal can't be dated in the past.
  `JTP_TODAY` now sets the date only in the toolkit's own tests. A record past its 30 days is skipped, never
  deleted: only the owner's yes or `forget-estimates` removes it. `jtp doctor` says when the computer's clock is
  behind that date.
- **An honest re-price isn't "taken off".** An estimate re-priced on its own invoice and confirmed there is cleared
  up to its new figure: Call-out $89 to $99 is cleared, $89 to $1 is confirmed only up to $1. While that invoice is
  a draft, the old figure is checked at any amount. A genuine removal is still remembered.
- **A $0 line never asks** as a copy of an estimate ("Call-out (waived)").
- **The owner's question** names each matching estimate with its figure, once.
- **`jtp customer forget-estimates`** stops the checks against a customer's removed estimates, after the owner's
  two-step yes ("Forget these estimates…?"). `--dry-run` on a command that can't preview says so.
- **More retypes are recognised:** "Callout fee", "Call-outs" (a plural s), an Armenian օ, a Latin ɑ, small
  capitals (ᴀ) and a 0 or 1 standing for a letter. The duplicate-name warning folds accents and lookalike letters
  too ("Jane Doé").
- 107 automated tests on Python 3.9, 3.11 and 3.13, and a 47-check self-test.

## 1.0.3 (2026-10-19)

Fixes from an independent recheck of 1.0.2.

- **An estimate that left its invoice is recognised at any amount.** One taken off a draft, or on a draft or change
  order that was deleted or voided, is asked about when a line with its description turns up on another invoice
  for the customer, also inside a longer description. A copy re-priced by a cent, split into two lines or merged
  into a bigger one asks, until the owner's yeses add up to the estimate. An estimate still on its draft, change
  order or quote keeps needing the same figure, so ordinary lines with a common name aren't asked about.
- **A yes covers only the estimates it was about.** It no longer covers every later estimate of the same figure
  for that customer: a newly marked estimate asks again, and a yes about a copy lapses after 30 days.
- **Descriptions are compared more loosely:** "Callout" is "Call-out", and lookalike letters ("Cаll-out" with a
  Cyrillic а) and accents are folded.
- **The agent's guide** says when an estimate is asked about, but no longer spells out how copies are matched;
  that's in SECURITY.md. The SKILL says to keep the estimate mark when you change a quantity or price.
- **Renaming a customer** to another customer's name gets the same warning as adding one.
- 93 automated tests on Python 3.9, 3.11 and 3.13, and a 41-check self-test.

## 1.0.2 (2026-10-18)

Fixes from an independent review of 1.0.1.

- **Copied estimates are recognised by their figures.** A line with the same description and amount as an
  estimate the owner hasn't confirmed for the same customer is asked about when its invoice is issued, even without
  the mark. The question names it and says where the estimate is: on another draft that's still open, an unbilled
  change order or an accepted quote (all new), or on a draft or change order that was deleted or voided. An
  invoice with none of those figures isn't asked, and once the owner confirms a figure for a customer, it isn't
  asked about again. Before, a yes to any invoice for that customer cleared estimates the owner never saw, and an
  open estimate made every line with the same name ask, again after every yes.
- **An estimate changed or taken off a draft stays on record.** The draft's own question names it, and the same
  figure on another invoice still asks, also after the draft is issued, deleted or voided. Before, an estimate
  moved from one invoice to another was never asked about.
- Descriptions are matched without case, spacing or punctuation ("Call out" is "Call-out").
- **Voiding a change order remembers its estimates**, the same way as a voided draft invoice.
- **Phone numbers.** When the phone changes, "+44 (0)20 7946 0100" is recognised as the old number, and a number
  that only ends like the old one (a local "555-0100", an account number) is named in a warning rather than left
  without a word.
- **A second customer with the same name gets a warning** naming the first, so one person's history isn't split
  across two records.
- SECURITY.md states the estimate check's known limit (figures copied to a different customer, or under a
  different description), and its lines fit the wrap width.
- 87 automated tests on Python 3.9, 3.11 and 3.13, and a 39-check self-test.

## 1.0.1 (2026-10-18)

Fixes from three rounds of an independent review.

- **Only the owner's plain yes counts.** Issuing an invoice that has estimated figures asks the owner the exact
  question and returns a one-time code. It goes ahead only when their whole reply is a plain yes ("yes", "yes,
  they're final") and comes back with that code. "ok", "k", "sure", a thumbs-up, "yes jk", "yeah right", "that's
  right" on its own or "yes, next week" is asked again; "absolutely not" stops it. The code is tied to that invoice
  and those figures, so a changed figure is asked about afresh, and it works once. Editing the invoice can't take
  an estimate off: it stays marked until the owner's yes. Progress, deposit and balance invoices built from an
  estimated quote ask too, and a draft deleted or voided with unconfirmed estimates is remembered, so the
  customer's next invoice asks.
- **Voiding an invoice and making a credit note ask the owner first**, the same way, since neither can be undone.
- **A no is never reported as success.** A no on an invoice that's already issued (or void) says so and changes
  nothing, instead of answering ok.
- **Dry runs change nothing.** `invoice issue --dry-run` shows the issued invoice and the owner's question without
  issuing it or using up a code. Commands that can't preview now refuse `--dry-run` instead of going ahead.
- **A price book item keeps its kind.** A permit (a fee) on a line marked material is billed and taxed as a fee,
  with a warning.
- **Payment instructions follow your contact details.** A new business phone number or email replaces the old one
  in the payment instructions (the same number written another way too, but never an account number that only
  ends with the same digits), and any other address they still give is named.
- **The money brief names every open invoice** with its due date, overdue first (the first 10, then a count).
- **Chat totals show the currency code** where the symbol is shared ("$126.00 CAD"), the same as the PDFs.
- **The demo sets its town's time zone**, so "today" is the demo's day.
- **Change orders after full progress billing.** Once a quote is fully billed, `invoice create --job` bills the
  approved change orders and extras still due, instead of saying there's nothing left.
- Smaller fixes: dates in messages are written the owner's way ("18 Oct 2026"); a setup hint no longer mentions a
  `--dry-run` that `profile set` doesn't have; the price book warning says when a line's own tax codes still
  apply; `doctor` on an empty folder says how to start; the guide says never to edit the data files by hand, and
  to get the owner's OK before `co void` or `payment undo`.
- **The terms never sit on a page of their own.** When a quote's or invoice's terms don't fit under the last box
  (acceptance or payment), that box moves to the next page with them. The demo's good/better/best quote on
  Letter paper no longer ends with a page holding only the terms.
- **The README is honest about installs.** It leads with what works today (a clone, Claude Code, a local Cursor
  copy), marks the Grok Bot, Cursor Marketplace and Grok Build listings as coming soon, and says how updates
  arrive.
- `jtp pricebook import items.csv`, as its example shows it, no longer crashes (the file name went to the wrong
  place).
- The agent no longer suggests searching the home folder for the toolkit.
- 75 automated tests on Python 3.9, 3.11 and 3.13, and a 34-check self-test.

## 1.0.0 (2026-10-15)

First release.

- Quotes with good / better / best options, deposits (optionally only above a job size), valid-until dates,
  revisions, and DRAFT previews before anything is saved. Accepting one quote retires the other open quotes
  on that job, so they stop getting follow-ups.
- Change orders with recorded customer approval, billed automatically once approved.
- Invoices from accepted quotes, progress and deposit invoices, time and materials, or quick one-offs. Tax is
  worked out line by line, so invoices add up to exactly what the customer agreed to, to the cent.
- Estimated figures are labelled on quotes, and an invoice won't issue until the owner confirms them; the
  owner's own words are kept on the invoice.
- Payments and deposits (by amount or as a percent of the accepted quote), held deposits applied automatically,
  receipts, credit notes and voids with gap-free numbering.
- Follow-ups: quote check-ins, a five-stage payment reminder ladder, review requests, service reminders.
  Every message is approved by the owner before it is sent.
- Money brief, receivables aging, job profit, pricing insight (real vs quoted hours), tax charged (accrual or cash
  basis), sales, CSV exports with credit notes included.
- A copy of every quote, change order, invoice and credit note exactly as it was sent (or as quoted, when a
  quote is accepted without being marked sent).
- Paid documents carry a clean "PAID" badge that copies and reads as one word; receipts show who paid.
- Any currency (0, 2 or 3 decimals), with the currency code beside totals where a symbol like $ is shared, any
  taxes (exclusive, inclusive or compound), any date format, letter or A4, and translatable document wording. Payment instructions can name the quote or invoice number.
- Built-in PDF engine with no dependencies, plus HTML output and an optional local browser engine for any language.
- Standard library Python 3.9+, local files only, no network calls, 49 automated tests and a built-in self-test. The demo business looks local
  in USD, CAD (BC GST + PST), GBP (VAT) and AUD (GST).
