# Quoting: from a voice note to an accepted quote

Speed wins jobs. The first clear, professional quote often gets the work, so aim for one round of
questions and a quote ready within a couple of minutes.

## 1. Capture

From the owner's message, voice note transcript, photos or notes, pull out:

- **Customer**: `jtp customer list --search "<name or phone>"`. If they're new:
  `jtp customer add --name ... --phone ... --email ... --address "line 1; line 2"`.
  Adding is safe to repeat: a matching email or phone returns the existing customer.
- **Existing work**: `jtp job list --customer C-0001`. If there's already a job or an open quote for
  this work, revise that quote or create the new one with `--job J-0003`, so the customer doesn't
  get follow-ups for both. (The toolkit warns when a customer already has open quotes.)
- **Site address** if it's different from their billing address (`"site_address"` in the spec).
- **Scope**: what will be done, in words the customer understands.
- **Materials and equipment**, with the owner's cost if they said it.
- **Labour**: hours, people, and which rate (standard, after-hours, apprentice).
- **Conditions**: access, permits, disposal, old or unknown conditions, timing.
- **Job type tags** (e.g. `water heater`, `panel upgrade`, `drain`) so pricing insight works later.

Photos: describe what you see that affects price (corrosion, access, code issues) and confirm with
the owner before pricing it in.

## 2. Price it

Use, in this order:

1. What the owner said. **A bare figure for a part ("the tank's about 1050") is their cost.** That's
   how trades usually talk, so use `"unit_cost"` and their markup is added. Use `"unit_price"` only
   when they say it's the price to the customer ("charge her 1,500", "sell price", "installed price").
   Always say which way you read it in the preview ("I took 1,050 as your cost + 30% = 1,365"),
   because a wrong guess moves the price by the whole markup.
2. The price book: `jtp pricebook list --search "<words>"`. Reference items with `"pricebook": "PB-0003"`
   (or the exact name). Cost plus their markup, or a fixed price, is applied automatically.
3. Their labour rates: `{"kind": "labour", "description": "...", "qty": 4}` uses the standard
   rate; add `"rate_key": "after_hours"` for others.
4. History: `jtp report pricing --tag "<type>"` shows how long this kind of job really took versus
   what was quoted. If they usually run over, say so and suggest the realistic hours.
5. Only if needed, a researched estimate (supplier website, catalogue). Mark it `"estimate": true`,
   tell the owner the source, and get their OK before sending. The quote prints "(estimate)" next to
   it: remove the flag once the owner confirms the figure, or keep it if the line really is an
   allowance to be billed at the actual cost.

### Forgotten-costs checklist

Scan this and ask about the 1 to 3 items most likely to apply. **Ask; don't add them yourself.**
Only put scope, items or promises on a quote ("includes haul-away of the old tank") that the owner
said or confirmed. These are the usual reasons small jobs lose money:

- Travel, call-out or trip charge; parking, tolls, ferries
- Disposal, haul-away, dump or recycling fees
- Permits, inspections, utility locates, engineering
- Consumables and small parts (fittings, sealant, fasteners, tape)
- Equipment rental (lifts, scaffolding, drain camera, excavator)
- Hard access (attic, crawlspace, heights), after-hours or weekend work
- Protection and clean-up (floor protection, dust control)
- Making good: patching, painting, flooring, landscaping (or list it as excluded)
- Return visits: inspection day, commissioning, follow-up
- Subcontractors (electrician, drywaller, glazier)
- Risk on old or unknown conditions: price a contingency or state the exclusion clearly

### Good / better / best

Offer options when there's a natural choice: equipment grade, scope (repair vs replace), or
warranty length. Customers given options pick the middle one often, and it raises the average job.

- 2 or 3 options, plain names: "Good: like-for-like 40 gal", "Better: high-efficiency 50 gal".
- Mark one `"recommended": true`: the one the owner would put in their own home.
- One sentence per option on what's different and why it matters (`"description"`).
- Keep the scope and labour honest per option; don't pad the cheap one to push the expensive one.

## 3. Write the spec and preview

```json
{
  "title": "Water heater replacement",
  "site_address": "88 Cedar Rd, Surrey BC",
  "intro": "Thanks for having us out today. Here are your options.",
  "options": [
    {"name": "Good: like-for-like 40 gal", "lines": [
      {"kind": "material", "pricebook": "40 gal gas water heater", "qty": 1},
      {"kind": "labour", "description": "Remove old tank, install and test new heater", "qty": 4}
    ]},
    {"name": "Better: 50 gal high-efficiency", "recommended": true,
     "description": "Lower running costs and faster recovery.", "lines": [
      {"kind": "material", "pricebook": "50 gal HE water heater", "qty": 1},
      {"kind": "material", "description": "Thermal expansion tank", "qty": 1, "unit_cost": 65},
      {"kind": "labour", "description": "Remove old tank, install and test new heater", "qty": 4.5}
    ]}
  ],
  "notes": "Work can usually be booked within 3 business days."
}
```

Line kinds: `labour`, `material`, `equipment`, `subcontract`, `fee`, `other`, `discount`
(`"percent"` or `"amount"`), and `text` (a note line with no price). One option? Use `"lines"`
instead of `"options"`.

**Taxes** follow the owner's profile automatically, so leave `tax_codes` out. Override a line
(`"tax_codes": ["GST"]`, or `[]` for no tax) only when the owner tells you that item is taxed
differently. Never decide a tax treatment yourself.

**Deposits** follow the profile too (`terms.deposit_percent`, and `terms.deposit_min_total` if the
owner only takes deposits on bigger jobs). Set `"deposit_percent"` or `"deposit_amount"` in the spec
only when the owner asks for something different on this job.

```bash
jtp quote create --customer C-0001 --tags "water heater" --spec quote.json --dry-run
```

Nothing is saved, but the preview also writes `previews/PREVIEW.pdf` (stamped DRAFT), so the owner
can see exactly what the customer will get. It works before the customer is added, too (leave out
`--customer`). Show the owner each option's total, the deposit, how you read any bare prices (cost
or sell), and any warnings (estimates especially). Then:

```bash
jtp quote create --customer C-0001 --spec quote.json        # or --job J-0004 if the job exists
```

The output has the quote, a `summary`, the PDF path, and `pricebook_suggestions`. Offer: "Save the
40 gal heater at $980 cost to your price book?" Then `jtp pricebook add ...` on yes.

Tags: put them on the job (`jtp job update J-0004 --tags "water heater"`) so pricing insight can
group similar jobs.

## 4. Send (with approval)

Draft using the quote message in messages.md. Mention each option's total, the deposit, the
valid-until date, and how to accept ("Just reply with A, B or C"). Show the draft and PDF, and
send only after the owner says yes. Use their preferred channel (see integrations.md). Then:

```bash
jtp quote mark-sent Q-0001 --channel email --to jane@example.com
```

That also keeps a copy of the exact PDF that went out in `output/sent/` (`sent_copy`), which settles
"that's not what you quoted me" later.

## 5. Changes before acceptance

```bash
jtp quote show Q-0001 --as-spec > q.json     # edit the spec
jtp quote revise Q-0001 --spec q.json        # same number, next revision; send it again
jtp quote extend Q-0001 --days 14            # expired or about to expire
```

## 6. Yes or no

- **Yes**: `jtp quote accept Q-0001 --option B --by "Jane Doe" --method email --note "Reply 14 Oct"`.
  Record exactly how they accepted (email reply, signed PDF, text). A job bills one quote, so any
  other open quotes on the same job are marked as replaced automatically and stop getting follow-ups;
  tell the owner which ones (the output lists them). If the customer has open quotes on *other* jobs,
  the output asks (`ask_owner`) whether this acceptance replaces any of them. If the owner says yes,
  retire each one: `jtp quote supersede Q-0002 --with Q-0004`. If the quote was never marked sent,
  the PDF as quoted is kept in `output/sent/` (`as_quoted_copy`) before it's redrawn as accepted. If there's a deposit, the output
  says how much (`deposit_due_display`); send a deposit request if they haven't paid (messages.md). When it arrives,
  record it with `jtp payment record --job J-0004 --deposit --amount <amount> --method "e-Transfer"
  --reference <their reference>`. If the owner gives a percentage instead ("she paid the 25%"), use
  `--percent 25` in place of `--amount`, so the toolkit works out the amount from the accepted option.
  Offer to book the work in the calendar if one is connected.
- **No**: `jtp quote decline Q-0001 --reason "Went with a cheaper quote"`. The reason feeds win-rate
  insight, so ask for it gently.
- **Silence**: the follow-up schedule handles it (getting-paid.md).

## Writing good quotes

- Describe outcomes, not just parts: "Remove the leaking tank and install a new 50 gal
  high-efficiency heater, with new connectors and expansion tank, tested and working."
- Put what's included and excluded in `notes` (or `text` lines) to avoid disputes.
- Match the customer's language and the owner's tone.
- Never put costs, markups or internal notes in a quote.
