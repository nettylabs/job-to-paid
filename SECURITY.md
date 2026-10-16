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

## Reporting a vulnerability

Please open a private security advisory on this repository (GitHub → Security → Report a vulnerability), or an
issue if it's not sensitive. We aim to respond within a few business days.
