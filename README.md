<p align="center"><img src="assets/logo.png" width="96" height="96" alt="Job to Paid logo"></p>

<h1 align="center">Job to Paid</h1>

<p align="center"><b>Quotes, change orders, invoices and getting paid, for trades and service businesses.</b><br>
Talk through the job. Get a professional quote, the extras approved in writing, the invoice out, and polite
reminders until it's paid. Any country, any currency, any tax setup.</p>

---

Job to Paid is an agent plugin for **Grok Bot**, **Cursor**, **Grok Build** and **Claude Code** (and any agent that
reads [Agent Skills](https://agentskills.io)). It gives the agent the know-how of a good office manager plus a
local toolkit that does the paperwork and every calculation exactly. There's no account to create, no server, and
no subscription. Your records stay in plain files on your own (or your bot's) computer.

<p align="center"><img src="assets/sample-quote.png" width="560" alt="A sample good/better/best quote made by Job to Paid"></p>

## What it does

- **Voice note to quote in minutes.** "Quote Jane for swapping her 40 gallon heater, 4 hours, heater's 980." You
  get a branded PDF quote priced from your rates, markup and price book, with **good / better / best** options
  when there's a natural choice.
- **No more forgotten costs.** Before pricing, it checks a list of the usual money-losers (disposal, permits,
  consumables, access, return visits) and asks about the few that apply.
- **Change orders that protect you.** Found a seized valve? It writes the change, gets the customer's YES on
  record before the work, and bills it automatically.
- **Every kind of invoice.** Bill the accepted quote plus approved changes plus logged extras, bill in progress
  stages, take deposits, bill time and materials, or send a quick one-off. Deposits come off automatically.
- **Gets you paid without the awkward chasing.** Quote follow-ups and a five-step reminder ladder (friendly
  heads-up, due, polite, firm, final), each drafted for your one-tap OK. Snooze when they promise to pay Friday.
- **Morning money brief.** Who owes you, what's overdue, quotes waiting, and **finished work you haven't billed
  yet**.
- **Know your numbers.** Profit per job and per job type, real hours vs quoted hours ("water heaters take you 5.3 h,
  you quote 4.5"), quote win rate, days to get paid, and tax charged for your return (on invoices issued or on
  payments received). CSV exports for your accountant.
- **Repeat business.** Service reminders (annual furnace service, backflow tests) and review requests after
  happy, paid jobs.

## Works anywhere

Nothing is hard-coded to one country. During setup you tell it your currency, taxes and paperwork style once:

- Any currency, including 0-decimal (JPY, KRW) and 3-decimal (KWD, BHD) ones, with your separators and symbol.
  Where a symbol is shared (like $), totals also show the code, e.g. "$1,234.50 CAD", so customers know which dollar.
- Any taxes: one or several, on everything or only on materials, prices before tax or including tax, even compound
  taxes. GST/HST/PST, VAT, sales tax, consumption tax: you name them, set the rates and confirm them.
- Your registration numbers on every invoice, your date format, letter or A4 paper, and documents in your language.
- Gap-free document numbering, locked invoices and proper credit notes, which many tax authorities expect.

It never decides your taxes for you and isn't tax or legal advice. It applies exactly what you confirm.

## Safe by design

- **You approve every message.** Nothing goes to a customer until you say so. It never charges cards or moves money.
- **Exact math.** Every total, tax, discount, deposit and balance comes from a tested calculator (Python
  `Decimal`, correct rounding per currency), never from the AI's head. Tax is worked out line by line, so the
  invoice always matches the quote plus approved changes, to the cent.
- **Your margins stay private.** Costs, markups and internal notes never appear on customer documents.
- **No network, no credentials, no telemetry.** See [SECURITY.md](SECURITY.md).
- **Audit trail.** Every change is logged, a copy of each document is kept exactly as it was sent, and one command
  makes a zip backup.

## Install

**Grok Bot**: open **Plugins** in the sidebar, search for **Job to Paid**, and add it. Then say "set me up".

**Cursor**: install **Job to Paid** from the Cursor Marketplace. For a local copy:
`git clone https://github.com/nettylabs/job-to-paid ~/.cursor/plugins/local/job-to-paid`, then reload.

**Grok Build**: `grok plugin install nettylabs/job-to-paid --trust`

**Claude Code**: `/plugin marketplace add nettylabs/job-to-paid`, then `/plugin install job-to-paid@nettylabs`

**Any agent with a terminal**: point it at this repository and say: *"Clone
https://github.com/nettylabs/job-to-paid and follow skills/job-to-paid/SKILL.md."*

Requires Python 3.9 or newer, standard on most Linux machines, including typical agent cloud computers. On a Mac or
PC without it, install it from python.org. Nothing to `pip install`.

## Try it in two minutes

Ask your agent:

1. "Set up a Job to Paid **demo** business and show me the money brief." (runs `jtp demo` in a separate folder)
2. "Quote Jane Doe for replacing her water heater: good, better and best options."
3. "She picked B. She paid a 25% deposit by bank transfer."
4. "We found a seized gas valve. Write a change order for the valve plus 45 minutes."
5. "Job's done. I logged 5 hours and a $28 flex connector as an extra. Invoice it."
6. "Who owes me money? Draft today's reminders."

Or run the toolkit yourself:

```bash
python3 skills/job-to-paid/scripts/jtp.py selftest                 # built-in checks: math, a full job, PDFs
python3 skills/job-to-paid/scripts/jtp.py --home /tmp/jtp-demo demo
python3 skills/job-to-paid/scripts/jtp.py --home /tmp/jtp-demo brief --text
```

## How it's built

```text
skills/job-to-paid/
  SKILL.md            what the agent does, and the golden rules
  references/         setup, quoting, jobs & change orders, invoicing, getting paid, messages, reports, integrations, CLI
  scripts/jtp.py      the toolkit (JSON in, JSON out; `jtp --help` for everything)
  scripts/jobtopaid/  money math, documents, follow-ups, reports, PDF and HTML rendering
commands/             /quote, /invoice, /money-brief, /job-to-paid-setup
tests/                python3 -m unittest discover -s tests
```

It works with other connected apps when they're there (Gmail or the bot's own email, WhatsApp Business,
Stripe or Square payment links, Xero, QuickBooks Online, Google Calendar and Drive, DocuSign, Slack) and works
fully without them.

## Support

Questions, bugs or ideas: [open an issue](https://github.com/nettylabs/job-to-paid/issues).

## License

MIT © 2026 Verkholm Inc. Netty Labs is a brand of Verkholm Inc. Job to Paid helps you prepare business documents;
you're responsible for checking them and for your own tax and legal obligations.
