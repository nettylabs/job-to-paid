# Message templates

Starting points. Adapt them to the owner's voice (ask once how formal they like to be), the customer's
language, and the channel. For text or WhatsApp, use the short version. Fill every `{...}` from the
toolkit output; never type amounts by hand. Always show the draft to the owner first.

Signature: `{business.owner_name}, {business name}, {phone}` (from `jtp profile show --section business`;
if `owner_name` is empty, ask the owner once and save it with `jtp profile set business.owner_name "Sam"`).

## Quote

**Email subject:** Your quote from {business}: {title}

> Hi {first name},
>
> Thanks for having us out. Your quote for {title} is attached.
>
> {one option: "The total is {total}, including tax."
>  several: for each, "Option {A}: {name}: {total}" (mark the recommended one)}
>
> {if deposits: "A deposit of {deposit_due} books the work in" (several options: each option's
> deposit; say which need none). How to pay is on the quote.} The price is good until {valid until}.
>
> {one option: "To go ahead, just reply YES." several: "To go ahead, just reply with the option you'd
> like."} Any questions, or anything you'd like changed, just let me know.
>
> {signature}

**Short (WhatsApp, or email from a phone):** Hi {first name}, your quote for {title} is attached:
{one option: "{total} incl. tax. Reply YES to book it in." several: "{options and totals}. Reply
with A, B or C to book it in."} Good until {valid until}. {owner name}, {business}

**Text message (SMS can't carry the PDF):** Hi {first name}, it's {owner name} from {business}. Your
quote for {title} comes to {total, or each option's total}. I've emailed the full quote to {email}
{no email: "Want it by email or WhatsApp?"}. Reply {YES, or A, B or C} to book it in.

## Quote follow-ups

- **check_in** (about 2 days): Hi {first name}, just checking the quote for {title} came through
  OK and makes sense. Any questions, just ask.
- **helpful_nudge** (about a week): Hi {first name}, any thoughts on the {title} quote? If the
  budget or timing doesn't suit, I can suggest options. {recommended option and total} is the one
  I'd go with.
- **last_check** (about 2 weeks): Hi {first name}, last check-in on the {title} quote. It's good
  until {valid until}. If you've gone another way, no problem at all, and a quick reply helps me
  plan my schedule.

## Deposit request

> Hi {first name}, great, we're booked in for {title}! To lock in the date, the deposit is
> {deposit_due for the accepted option} ({how to pay, payment link first}). I'll confirm as soon as it's in.

## Change order

> Hi {first name}, while working on {job title} we found {reason}. Fixing it is {total} including
> tax (details attached). Can you reply YES to go ahead, or call me if you'd like to talk it through?
> We'll hold off on that part until we hear from you.

**Nudge** (if waiting): Hi {first name}, just checking you saw the change for {job title}. We
need your OK before doing that part.

## Invoice

**Email subject:** Invoice {number} from {business}: {title}

> Hi {first name},
>
> Thanks again for choosing us for {title}. Your invoice {number} for {total} is attached
> {if deposit or payments: ", with {paid} already received, so {balance} is left to pay"}.
>
> It's due on {due date}. {payment link: "You can pay online here: {link}"} {or the payment methods}
>
> {if warranty: one line about the warranty}
>
> {signature}

**Short:** Hi {first name}, invoice {number} for {title}: {balance} due {due date}. {link or how
to pay}. Thanks! {owner name}

## Payment reminders (one per stage)

- **friendly_heads_up** (before the due date): Hi {first name}, a friendly heads-up that invoice
  {number} ({balance}) is due on {due date}. {link or how to pay}. Thanks!
- **due_now** (due or just past): Hi {first name}, invoice {number} for {balance} was due {due
  date}. If it's already on its way, thank you and please ignore this. Otherwise: {link or how to
  pay}.
- **overdue_polite** (about 1 to 2 weeks): Hi {first name}, just following up on invoice {number}
  for {balance}, now {days overdue} days past due. Is everything OK with the work? If something's
  holding it up, let me know and we'll sort it out. {link or how to pay}
- **overdue_firm** (2 to 4 weeks): Hi {first name}, invoice {number} for {balance} is now {days
  overdue} days overdue and I haven't heard back. Please arrange payment this week ({link or how to
  pay}), or reply and let me know when to expect it. If you'd like to split it into instalments,
  I'm open to that.
- **final_notice** (last stage): Hi {first name}, this is a final reminder for invoice {number}:
  {balance}, {days overdue} days overdue. Please pay by {7 days from today} ({link or how to pay})
  or call me on {phone} to agree a plan. {Only if the owner asks: their next step, e.g. "After
  that, I'll need to look at other options to recover it."}

Never add late fees, interest or legal threats unless they're in the owner's written terms and the
owner explicitly asks.

## Credit note

Numbers from `jtp credit show CN-0003` (and `invoice_summary` from `credit create`).

> Hi {first name}, as promised, here's credit note {number} for {total} against invoice
> {invoice number} ({reason}). {refund_due: "As you'd already paid, I'll refund {refund_due} the
> same way you paid." balance left: "That brings what's left to pay to {balance}." nothing left:
> "That settles the invoice, so there's nothing more to pay."} Any questions, just ask.

**Short:** Hi {first name}, credit note {number} for {total} on invoice {invoice number} is
attached. {refund, new balance, or "Nothing more to pay."} {owner name}

## Thank-you and receipt

> Hi {first name}, payment received, thank you! Your receipt is attached. {if service interval: "I'll
> remind you when it's due for its next service."} Any problems with the work, just call.

## Review request (only if a review link is set)

> Hi {first name}, glad we could help with {title}. If you were happy with the work, a quick review
> would mean a lot to a small business like ours: {review link}. Thank you!

## Service reminder

> Hi {first name}, your {title} is due for its {interval} service around {service due}. Want me to
> book you in? Reply with a day that suits.

## Unsent documents

`send_quote` and `send_invoice` actions mean something was created but never sent. Ask the owner:
"{number} for {customer} hasn't been sent yet. Send it now?" Then use the quote or invoice template.
