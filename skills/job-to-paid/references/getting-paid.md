# Getting paid

## Recording money that came in

```bash
jtp payment record INV-0012 --amount 1543.50 --method "e-Transfer" --reference CA7781 --date 2026-10-14
jtp payment record --job J-0004 --deposit --amount 500 --method "Bank transfer"   # before invoicing
jtp payment record --job J-0004 --deposit --percent 25 --method "Bank transfer"   # 25% of the accepted quote
jtp payment receipt PAY-0009          # a PDF receipt to send the customer
jtp payment undo PAY-0009             # recorded by mistake
jtp payment list --from 2026-10-01
```

- A payment larger than the balance is refused. Record the balance and tell the owner about the
  overpayment.
- Recording the same payment twice in a row is caught (`"duplicate": true`). Use
  `--allow-duplicate` only when it really is a second payment for the same amount.
- When an invoice becomes fully paid, offer: a thank-you with receipt, and a review request if the
  review link is set (the follow-up list will suggest it).
- **Spotting payments**: if email, Stripe, Square, Xero or QuickBooks is connected, look for new
  payments (e.g. "e-Transfer received", "Payment received", payout notices), match them to open
  invoices by amount, name and reference, and ask the owner to confirm before recording each one.

## The follow-up list

```bash
jtp followups due
```

This returns today's actions, most urgent first. Each has the customer card (with a ready
`display_name`), amounts, PDF path, stage and `tone`. Dates come ready in the owner's format
(`due_date_display`, `valid_until_display`), and money actions carry `how_to_pay` (the owner's
payment methods, ready to paste) plus `payment_link` when one is set:

| action | What it means | What to draft (messages.md) |
|---|---|---|
| `payment_reminder` | Invoice reminder stage reached. Tone: `friendly_heads_up`, `due_now`, `overdue_polite`, `overdue_firm` or `final_notice` | The matching reminder |
| `escalate` | Very overdue, reminders are done | No message. Suggest a call, a payment plan, or the local small-claims or collections route (owner decides) |
| `send_invoice` / `send_quote` | Created but never sent | The invoice or quote message |
| `quote_followup` | Quote follow-up due. Tone: `check_in`, `helpful_nudge`, `last_check` | The matching follow-up |
| `quote_expired` | Past its valid-until date | Ask the owner: extend (`jtp quote extend`), follow up, or `jtp quote expire` |
| `change_order_followup` | Change order waiting for approval | The change order nudge |
| `review_request` | Paid job, review link set | The review request |
| `service_reminder` | Recurring service coming due | The service reminder |

If an action has a `heads_up`, the customer has since accepted a different quote. Ask the owner
before drafting anything; if the old quote was replaced, retire it with the command in the heads-up.

**The approval loop** (the morning routine does exactly this):

1. Draft every message, in the customer's language and the owner's voice, on the customer's
   preferred channel.
2. Show them together, numbered and short, with the amount and PDF for each, and ask "Send all, or
   tell me which to change or skip."
3. Send only what's approved. Then record each one:

```bash
jtp followups done INV-0012 --stage 2 --channel email
jtp followups done J-0004 --stage review --channel sms
jtp followups skip Q-0007 --stage 1 --note "Talked on the phone, deciding Friday"
jtp followups snooze INV-0012 --until 2026-10-24 --note "Promised to pay Friday"
```

Recording matters: it stops the same reminder coming back, and it's the paper trail if a
dispute ever happens.

## Reminder etiquette (built into the templates)

- Always friendly and factual. Assume the bill got buried, not that the customer is dodging it.
- Every reminder says the amount, the invoice number, the due date, and how to pay (payment link
  first if there is one), and invites them to reply if something's wrong with the work or the bill.
- No threats and no legal claims. Late fees or interest only if they're in the owner's written
  terms and the owner asks for them.
- If the customer replies with a problem, stop the reminders (`snooze`) and bring it to the owner.
- One message per stage. Never several reminders at once.

## The money brief

```bash
jtp brief          # JSON with everything
jtp brief --text   # ready-to-send short summary
```

It shows: what's owed and overdue, quotes waiting (and expiring), work not billed yet (finished
jobs, logged extras, approved change orders), jobs running over quoted hours, money in this week
and month, and the count of follow-ups waiting for approval. Lead with the one or two things that
earn money fastest, e.g. "INV-0003 is 26 days overdue, want me to send the firm reminder?" or
"The Smith job is finished but not invoiced, invoice it now?"
