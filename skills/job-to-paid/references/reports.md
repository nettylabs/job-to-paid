# Reports and insight

All reports are read-only. Money values come back already formatted in the owner's currency.

```bash
jtp brief                                   # the daily money brief (getting-paid.md)
jtp report aging                            # who owes what: current, 1-30, 31-60, 61-90, 90+ days
jtp report profit --from 2026-07-01 --to 2026-09-30
jtp report pricing --tag "water heater"     # real hours vs quoted hours for this kind of job
jtp report tax --from 2026-07-01 --to 2026-09-30               # add --basis cash if they report on payments
jtp report sales --from 2026-01-01          # invoiced and collected per month, quote win rate, days to pay
jtp job show J-0004                         # one job: quoted, billed, paid, owing, hours, costs, margin
jtp customer show C-0001                    # one customer: jobs, lifetime billed, owing
jtp search "jane water heater"              # find anything
jtp export invoices --from 2026-07-01 --to 2026-09-30    # CSV for the accountant
```

## Turning reports into advice

The owner wants decisions, not tables. Lead with the one change that earns the most.

- **Profit by job type** (`report profit` → `by_tag`): "Drain jobs make you 52%, water heaters 21%.
  Water heaters take 5.3 h on average but you quote 4.5. Quote 5.5 h next time?"
- **Pricing insight** (`report pricing --tag ...`): use it while quoting. If jobs keep running over,
  suggest realistic hours. If they finish faster, say there's room to be more competitive.
- **Win rate** (`report sales`): under about 30% may mean prices are high or follow-up is weak.
  Over about 80% may mean prices are low. Suggest small tests, never big jumps.
- **Days to pay**: if it's long, suggest a payment link, a deposit, or shorter terms.
- **Aging**: anything 30+ days needs a decision. Bring it up kindly.

Margins use the logged costs. Materials without receipts fall back to the cost figures on the
quote, and labour without logged hours uses the priced labour cost. Say so when it matters ("about
21%, based on quoted material costs"). Costs depend on the owner adding a cost to their labour rates.

## Tax report

`jtp report tax` totals the tax charged on invoices issued in the period, minus credit notes, per
tax, with the taxable sales (the accrual or invoice basis). Many small businesses report on a cash
basis instead: tax on the payments actually received in the period. Ask the owner which one they
use once and remember it; `jtp report tax --basis cash` splits each payment in the same proportions
as its invoice and lists deposits received but not invoiced yet. Always add: "Check this against
your accountant's numbers before you file. You may have other income to include." Never claim it
is a filing.

## Exports

`jtp export <invoices|lines|payments|customers|expenses|quotes> [--from] [--to] [--out file.csv]`
writes CSV files the owner can send to their accountant or import into a spreadsheet or accounting
app. Credit notes appear in the invoice and line exports as their own rows with negative amounts
(`type` = `credit_note`), so the columns add up to the real sales. If Google Drive or email is
connected, offer to send them there.
