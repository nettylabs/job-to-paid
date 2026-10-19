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
  - an estimate that left its invoice (taken off a draft, or on a draft or change order that was deleted or
    voided), by its description at any amount, also inside a longer description, so a re-priced, split or merged
    copy asks, until the owner's yeses add up to its amount.

  Descriptions are compared without case, spacing, punctuation, accents or lookalike letters. The owner's yes
  covers only the estimates it was about, for 30 days: a newly marked estimate with the same figure asks again.
  A code proves the question was asked, not who answered: the agent is trusted to ask the owner.
- **Known limit.** A copied estimate is recognised by its description and its customer. Figures copied to a
  different customer, a copy of an open estimate retyped at a different amount, or a copy under a different
  description aren't recognised. That would be the agent deliberately working against its instructions, which a
  confirmation step can't prevent.

## Reporting a vulnerability

Please open a private security advisory on this repository (GitHub → Security → Report a vulnerability), or an
issue if it's not sensitive. We aim to respond within a few business days.
