# Changelog

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
