# Security

## What this plugin does and doesn't do

- **No network access.** The toolkit (`skills/job-to-paid/scripts/`) is standard-library Python. It makes no
  network connections, sends no telemetry, and downloads or runs no remote code.
- **No credentials.** It needs no API keys, tokens or accounts, and it never reads environment secrets, SSH
  keys or `.env` files.
- **Local files only.** It reads and writes plain JSON, PDF, HTML and CSV files in one data folder (`$JTP_HOME`,
  else `/workspace/job-to-paid` when `/workspace` exists, else `~/job-to-paid`) and in the system temp folder.
- **One optional subprocess.** For PDFs in scripts the built-in fonts can't show (e.g. Polish, Greek, Japanese),
  it can print the locally generated HTML file with a Chrome, Chromium or Edge browser that's already installed,
  offline, with a throwaway profile. It never installs or downloads a browser. Use `--engine builtin` to turn this off.
- **No hooks, no MCP servers, no background processes.** The plugin is one skill, four slash commands and the toolkit.
- **Money is never moved.** The toolkit only records payments. The skill instructs the agent to send nothing to a
  customer and charge nothing without the owner's explicit approval.
- **The owner's yes is checked, not assumed.** Issuing an invoice that had estimated figures, voiding an invoice
  and making a credit note each stop with the exact question and a one-time code. They run only when the owner's
  own words are a plain yes ("ok", "sure" or "yes, next week" isn't) and come back with that code, which is tied to
  that action and its figures and works once. Editing an invoice can't take an estimate off: only the owner's yes
  when issuing does, and an estimate changed or taken off a draft is named in its question. Progress, deposit and
  balance invoices built from an estimated quote ask too. A line that repeats an estimate the owner hasn't
  confirmed for the same customer is asked about when its invoice is issued, even without the mark:
  - an estimate still on another draft, a change order or an accepted quote, by the same description and amount;
  - an estimate that left its invoice (changed or taken off a draft, or on a draft or change order that was deleted
    or voided), by its description at any amount, also inside a longer description, so a re-priced, split or merged
    copy asks. The owner's yes to such a line confirms the estimate up to that line's amount: a copy up to it isn't
    asked about again, however many there are, and yeses on separate invoices aren't added together; a bigger one is
    asked about, and a yes that reaches the estimate's own figure clears it. A yes on the estimate's own invoice
    counts too: re-priced there from $89 to $99 and confirmed, it's cleared; re-priced to $1, it's confirmed up to
    $1. It lapses 30 days after it left.

  A line of $0 or less never counts as a copy. Descriptions are compared without case, spacing, punctuation,
  accents, a plural s, lookalike letters or a 0 or 1 standing for a letter. The owner's yes covers only the
  estimates it was about, for 30 days: a newly marked estimate with the same figure asks again. Both 30 days are
  counted on the computer's clock, in the business's time zone (`locale.timezone`, checked against the time zone
  database), and the dates only ever move the way that asks more. An estimate that leaves its invoice is dated no
  earlier than the latest date the data folder was used on (`clock.json`), so setting the clock back can't date it
  in the past, and nothing ever moves a recorded date. Its 30 days count from the computer's date. The owner's yes
  counts from the computer's date when it's given, but never while it's dated later than the computer's date, and
  never once it's 30 days older than the latest date the folder was used on, so no clock setting makes it last
  longer. Invoices and quotes use the computer's date as it is. `JTP_TODAY` sets the date only in the toolkit's own
  tests. A record past its 30 days is skipped, never deleted: only the owner's yes removes one, or `jtp customer
  forget-estimates`, which asks them first. A code proves the question was asked, not who answered: the agent is
  trusted to ask the owner.
- **Known limit.** A copied estimate is recognised by its description and its customer. Figures copied to a
  different customer, a copy of an open estimate retyped at a different amount, or a copy under a different
  description aren't recognised. Setting the computer's clock ahead makes the 30 days run out early while it stays
  ahead, and once it's right again the estimate questions may come more often until the real date catches up. That
  would be the agent deliberately working against its instructions, which a confirmation step can't prevent.

## Reporting a vulnerability

Please open a private security advisory on this repository (GitHub → Security → Report a vulnerability), or an
issue if it's not sensitive. We aim to respond within a few business days.
