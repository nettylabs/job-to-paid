# Changelog

## 1.0.1 (2026-10-18)

Fixes from an independent review.

- **Only the owner's plain yes counts.** Issuing an invoice that still has estimated figures asks the owner the
  exact question and returns a one-time code. It goes ahead only when their whole reply is a plain yes ("yes",
  "yes, they're final") and comes back with that code. "ok", "k", "sure", a thumbs-up, "yes jk", "yeah right" or
  "yes, next week" is asked again; "absolutely not" stops it. The code is tied to that invoice and those
  figures, so a changed figure is asked about afresh, and it works once.
- **The terms never sit on a page of their own.** When a quote's or invoice's terms don't fit under the last box
  (acceptance or payment), that box moves to the next page with them. The demo's good/better/best quote on
  Letter paper no longer ends with a page holding only the terms.
- **The README is honest about installs.** It leads with what works today (a clone, Claude Code, a local Cursor
  copy), marks the Grok Bot, Cursor Marketplace and Grok Build listings as coming soon, and says how updates
  arrive.
- `jtp pricebook import items.csv`, as its example shows it, no longer crashes (the file name went to the wrong
  place).
- The agent no longer suggests searching the home folder for the toolkit.
- 56 automated tests on Python 3.9, 3.11 and 3.13.

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
