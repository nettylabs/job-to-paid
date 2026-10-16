# Changelog

## 1.0.0 (2026-10-15)

First release.

- Quotes with good / better / best options, deposits (optionally only above a job size), valid-until dates,
  revisions, and DRAFT previews before anything is saved. Accepting one quote retires the other open quotes
  on that job, so they stop getting follow-ups.
- Change orders with recorded customer approval, billed automatically once approved.
- Invoices from accepted quotes, progress and deposit invoices, time and materials, or quick one-offs. Tax is
  worked out line by line, so invoices add up to exactly what the customer agreed to, to the cent.
- Estimated figures are labelled on quotes and must be confirmed by the owner before an invoice is issued.
- Payments and deposits (by amount or as a percent of the accepted quote), held deposits applied automatically,
  receipts, credit notes and voids with gap-free numbering.
- Follow-ups: quote check-ins, a five-stage payment reminder ladder, review requests, service reminders.
  Every message is approved by the owner before it is sent.
- Money brief, receivables aging, job profit, pricing insight (real vs quoted hours), tax charged (accrual or cash
  basis), sales, CSV exports with credit notes included.
- A copy of every quote, change order, invoice and credit note exactly as it was sent.
- Paid documents carry a clean "PAID" badge that copies and reads as one word; receipts show who paid.
- Any currency (0, 2 or 3 decimals), with the currency code beside totals where a symbol like $ is shared, any
  taxes (exclusive, inclusive or compound), any date format, letter or A4, and translatable document wording. Payment instructions can name the quote or invoice number.
- Built-in PDF engine with no dependencies, plus HTML output and an optional local browser engine for any language.
- Standard library Python 3.9+, local files only, no network calls, 47 automated tests and a built-in self-test. The demo business looks local
  in USD, CAD (BC GST + PST), GBP (VAT) and AUD (GST).
