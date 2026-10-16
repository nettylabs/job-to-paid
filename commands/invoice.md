---
name: invoice
description: Bill a finished job (quote + approved change orders + extras, progress, or time and materials) with Job to Paid.
argument-hint: "[job, customer, or what to bill]"
---

Use the job-to-paid skill to invoice: $ARGUMENTS

Pick the right invoice type, preview it with --dry-run, tell me about any warnings (unapproved
change orders, unbilled time), then create and issue it and show me the PDF. Add a payment link if
a payment app is connected. Ask before sending it to the customer.
