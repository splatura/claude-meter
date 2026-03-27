# Deployment Guide

How to run claude-meter as a persistent service on a Linux server.

## Prerequisites

- Go 1.22+ (to build the binary)
- Python 3 (for analysis and dashboard generation)
- Git with SSH access to your GitHub repo (for dashboard publishing)

## Build and Install the Binary

```bash
cd /path/to/claude-meter
go build -o claude-meter ./cmd/claude-meter
cp claude-meter ~/.local/bin/claude-meter
```

## Systemd Service

Create `~/.config/systemd/user/claude-meter.service`:

```ini
[Unit]
Description=claude-meter proxy for Claude Code API traffic
After=network.target

[Service]
ExecStart=/home/<user>/.local/bin/claude-meter start --plan-tier max_5x
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

Enable and start:

```bash
systemctl --user daemon-reload
systemctl --user enable claude-meter
systemctl --user start claude-meter
```

Useful commands:

```bash
systemctl --user status claude-meter    # check status
systemctl --user stop claude-meter      # stop
systemctl --user restart claude-meter   # restart
journalctl --user -u claude-meter -f    # follow logs
```

## Point Claude Code at the Proxy

Add to your shell profile (e.g. `~/.zshrc`):

```bash
export ANTHROPIC_BASE_URL=http://127.0.0.1:7735
```

Any tool that uses the Anthropic API can be pointed at the proxy the same way.

## Multi-Client Source Tracking

The proxy identifies which client made each request by inspecting the
`User-Agent` header. Currently recognized sources:

| User-Agent pattern                          | Classified as  |
|---------------------------------------------|----------------|
| `claude-cli/* (external, cli)`              | `claude-code`  |
| `claude-code/* (external, cli)`             | `claude-code`  |
| `claude-cli/*` (without `(external, cli)`)  | `openclaw`     |
| `Bun/*`                                     | `openclaw`     |
| Contains `openclaw`                         | `openclaw`     |
| Empty                                       | `unknown`      |
| Anything else                               | raw User-Agent |

Note: openclaw's pi-ai library uses an Anthropic OAuth token and sends
`User-Agent: claude-cli/<version>` without the `(external, cli)` suffix
that the real Claude Code CLI includes. This is how the proxy distinguishes
the two.

This means any client routed through the proxy is automatically tagged.
The dashboard and CLI summary both show a "Per-Source Breakdown" / "By Source"
section with per-client call counts and token usage.

### Routing openclaw through the proxy

openclaw uses the `@mariozechner/pi-ai` library which calls the Anthropic API
via `streamAnthropic()`. Its base URL is configurable per-provider in
`openclaw.json`:

```json
{
  "models": {
    "providers": {
      "anthropic": {
        "baseUrl": "http://127.0.0.1:7735"
      }
    }
  }
}
```

Alternatively, set `ANTHROPIC_BASE_URL=http://127.0.0.1:7735` in openclaw's
environment. openclaw's runtime (Bun) sends `User-Agent: Bun/<version>` by
default, which the proxy auto-classifies as `openclaw`. No code changes to
openclaw are needed.

### Routing other clients through the proxy

Any Anthropic API client can be pointed at the proxy the same way — set its
base URL to `http://127.0.0.1:7735`. The proxy classifies the client
automatically from the `User-Agent` header. Unrecognized User-Agents are
stored as-is in the `source` field.

## Dashboard Publishing via Cron

The HTML dashboard is a static snapshot. A cron job keeps it up to date
and publishes to GitHub Pages.

Add to crontab (`crontab -e`):

```cron
PATH=/usr/local/bin:/usr/bin:/bin

# Publish claude-meter dashboard to GitHub Pages every 30 minutes
*/30 * * * * cd /path/to/claude-meter && rm -rf /tmp/claude-meter-dashboard && make dashboard >> /tmp/claude-meter-dashboard.log 2>&1
```

This runs `make dashboard`, which:
1. Generates `index.html` from the latest normalized data
2. Force-pushes it to the `gh-pages` branch

Check `/tmp/claude-meter-dashboard.log` if publishing fails.

## Rebuilding Normalized Data

If the normalizer changes (e.g. new fields are added), rebuild from raw logs:

```bash
systemctl --user stop claude-meter
rm ~/.claude-meter/normalized/*.jsonl
go build -o ~/.local/bin/claude-meter ./cmd/claude-meter
~/.local/bin/claude-meter backfill-normalized --log-dir ~/.claude-meter --plan-tier max_5x
systemctl --user start claude-meter
```

Raw logs under `~/.claude-meter/raw/` are never modified — normalization
can always be re-derived.

## Data Location

All data is stored under `~/.claude-meter/` with private permissions:

```
~/.claude-meter/
├── raw/              # raw HTTP exchanges (JSONL, date-partitioned)
└── normalized/       # derived structured records (JSONL, date-partitioned)
```
