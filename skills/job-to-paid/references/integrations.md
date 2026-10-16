# Working with connected apps

Job to Paid works fully on its own. If the owner has connected apps (connectors, plugins or MCP
tools) on this account, use them to save steps. Check which tools you actually have before you
offer anything, and never ask the owner to connect an app they don't need.

The toolkit itself never talks to these services. You do, with the owner's approval, then record
the result in the toolkit.

## Sending to customers

Choose the channel in this order: the customer's `preferred_contact`, then what the owner says, then
email if there's an address, then text.

- **Email**: your own email address if you have one (sends wait for the owner's approval), or a
  connected Gmail or Outlook. Create the email with the PDF attached, show it, send on yes. If you
  can only make a draft, make the draft and tell the owner it's ready in their drafts.
- **WhatsApp Business** (if connected): send the short message, plus the PDF if the tool supports
  documents. Many trades customers outside North America expect WhatsApp.
- **SMS** (if a texting tool is connected): short message with the payment link or "PDF sent by
  email".
- **Nothing connected**: give the owner the message text and the PDF to send themselves, then record
  it as sent once they confirm.

After sending: `jtp quote|invoice|co mark-sent <ID> --channel <email|sms|whatsapp|...> --to <address>`.

## Getting paid online

- **Stripe**: create a Payment Link (or a Stripe invoice) for the invoice **balance**, in the
  invoice's currency, with the description "Invoice {number}: {title}". Then
  `jtp invoice set INV-0012 --payment-link <url> --external stripe=<id>`. The PDF gets a Pay online
  button. Check for payment later and record it with `jtp payment record` (owner confirms).
- **Square**: create a Square invoice for the balance (it carries its own pay link), then store the
  link and id the same way.
- **X Money and other wallets**: only for the *owner* paying suppliers if they ask. Never use them to
  move customer money.
- Never take card numbers in chat, and never charge a saved card.

## Accounting apps

- **Xero** (read and write): after issuing, offer to create the matching sales invoice in Xero (same
  number, lines, tax rates and due date) and store the id: `--external xero=<id>`. Later, check
  Xero for payments and record them here (owner confirms). If tax rates don't map cleanly, ask
  instead of guessing.
- **QuickBooks Online**: where the connector is read-only, use it to import customers
  (`jtp customer add`) and products and services with prices (`jtp pricebook add`), and to spot
  payments. For bookkeeping, send the accountant the CSV exports (`jtp export invoices`), or enter
  invoices in QuickBooks if the owner prefers.
- **Others / none**: `jtp export` CSVs work with any spreadsheet or accounting app.

## Scheduling

- **Google Calendar / Outlook / Cal.com**: after a quote is accepted, offer to book the job. Put the
  job number, customer phone and site address in the event.

## Files and backups

- **Google Drive / OneDrive / Box**: offer a "Job to Paid" folder for sent PDFs and a weekly
  `jtp backup` zip.

## Signatures

- **DocuSign** (or similar): for large quotes or change orders the owner wants signed formally, send
  the PDF for signature, then record `jtp quote accept ... --method docusign --note "<envelope id>"`.

## Team chat (Slack and the like)

- Crew can message the bot: "3 h on the Smith job", "used 2 valves". Log them with `jtp job log`
  and confirm briefly.
- Only the owner approves customer messages, discounts, credit notes and voids.
- In channels, don't post customer contact details or margins. Send those to the owner directly.
