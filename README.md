# z2w — Zendesk → Wasabi Offloader

**z2w** is a production automation stack that continuously offloads Zendesk ticket attachments and inline images to **Wasabi S3-compatible storage**, rewrites the original comment URLs inside Zendesk, and redacts the source files — reducing Zendesk storage costs with zero manual effort.

The project ships three sub-components:

| Component | Path | Stack |
|---|---|---|
| **Offloader daemon + Admin panel** | `/opt/z2w/` | Python 3 · Flask · APScheduler · SQLite |
| **Ticket Explorer** | `explorer/` | Next.js 16 · Tailwind v4 · shadcn/ui |
| **UX sandbox** | `uz/` | Next.js 16 · Tailwind v4 · Base UI |

---

## Table of Contents

1. [Features](#features)
2. [Architecture](#architecture)
3. [Installation (Debian/Ubuntu)](#installation-debianubuntu)
4. [Configuration](#configuration)
5. [Running as a Systemd Service](#running-as-a-systemd-service)
6. [Admin Panel](#admin-panel)
7. [How It Works](#how-it-works)
8. [Ticket Explorer (Next.js)](#ticket-explorer-nextjs)
9. [Backup System](#backup-system)
10. [Troubleshooting](#troubleshooting)
11. [Security](#security)
12. [Maintenance](#maintenance)

---

## Features

| Feature | Detail |
|---|---|
| **Continuous offload** | Runs every 5 minutes via APScheduler |
| **Inline image tracking** | Detects and migrates `<img>` srcs pointing to Zendesk CDN |
| **Ticket backup** | Separate job archives closed-ticket metadata to Wasabi |
| **Recheck-all report** | Full history scan to catch any tickets missed in normal runs |
| **Storage Usage page** | Per-bucket live size query from Wasabi |
| **Smart notifications** | Telegram / Slack / email reports sent only when work was done |
| **OAuth login** | Microsoft Entra ID (OIDC) + local username/password fallback |
| **Dark mode UI** | Figtree font, oklch colour system, responsive sidebar |
| **SQLite WAL mode** | Concurrent read/write with minimal locking |
| **Per-ticket logging** | Every action logged to `offload_logs` + `processed_tickets` tables |
| **Configurable rate limits** | `MAX_ATTACHMENTS_PER_RUN`, `TICKET_BACKUP_MAX_PER_RUN`, daily limits |
| **Backup system** | Daily tar.gz snapshots of database + logs, archived to Wasabi |
| **SSL** | Self-signed cert auto-generated on first run; replaceable with CA cert |

---

## Architecture

```
/opt/z2w/
├── main.py                   # Flask app factory, routes, OAuth
├── admin_panel.py            # Flask Blueprint: dashboard, tickets, logs, settings, explorer
├── offloader.py              # Core offload engine (download → upload → patch → redact)
├── scheduler.py              # APScheduler jobs (offload every N min, backup daily)
├── database.py               # SQLite helpers, WAL mode, schema migrations
├── config.py                 # .env loader, all runtime defaults
├── zendesk_client.py         # Zendesk REST API wrapper
├── wasabi_client.py          # boto3/S3 wrapper for Wasabi
├── oauth_auth.py             # Microsoft OIDC flow (MSAL)
├── backup_manager.py         # Tar/upload backup logic
├── ticket_backup_manager.py  # Closed-ticket metadata backup to Wasabi
├── email_reporter.py         # SMTP summary emails
├── telegram_reporter.py      # Telegram Bot API summaries
├── slack_reporter.py         # Slack Incoming Webhook summaries
├── logger_config.py          # Rotating file + console logging
├── password_generator.py     # Admin password hash utility
├── generate_ssl_cert.py      # Self-signed cert generator
├── create_favicon.py         # Favicon generator
├── bulk_inline_offload.py    # One-shot bulk inline migration tool
├── bulk_inline_remaining.py  # Resume tool for interrupted inline migrations
├── bulk_reoffload.py         # Re-offload tickets in bulk
├── bulk_redact_zendesk.py    # Bulk redaction of already-uploaded attachments
├── mass_offload.py           # High-throughput batch offload utility
├── templates/                # Jinja2 HTML templates (dark mode)
│   ├── base.html             # Shared layout, sidebar, CSS design system
│   ├── dashboard.html        # Stats + recent offload log
│   ├── tickets.html          # Processed tickets table (paginated)
│   ├── logs.html             # Offload logs (paginated, filterable)
│   ├── settings.html         # Live .env editor
│   ├── login.html            # Login page (password + OAuth button)
│   ├── storage.html          # Wasabi storage usage
│   ├── ticket_backup.html    # Ticket backup status
│   └── recheck_report.html   # Recheck progress feed
├── static/                   # Favicon, logo assets
├── logs/                     # Daily rotating log files (archived monthly)
├── backups/                  # Local backup archives
├── tickets.db                # SQLite database (auto-created)
├── requirements.txt
├── run_debian.sh             # Quick start script for Debian
├── setup_debian.sh           # Full system setup script
└── .env                      # Runtime configuration (not in git)

explorer/                     # Ticket Explorer Next.js app
├── src/
│   ├── app/                  # Next.js App Router pages
│   ├── components/           # UI components (ExplorerShell, panels, shadcn/ui)
│   ├── hooks/                # Custom React hooks
│   └── lib/                  # API client, storage utils
└── package.json

uz/                           # UX sandbox Next.js app
├── app/                      # Next.js App Router pages
├── components/               # UI components (Base UI, shadcn/ui)
└── lib/                      # Shared utilities
```

---

## Installation (Debian/Ubuntu)

### Prerequisites

- Debian 12 / Ubuntu 22.04+ (64-bit)
- Python 3.11+
- Node.js 20+ (for `explorer` / `uz` only)
- `sudo` access
- A Zendesk Admin API token
- A Wasabi account + bucket
- (Optional) Microsoft Entra App Registration for OAuth SSO

### 1. Install system packages

```bash
sudo apt update && sudo apt install -y python3 python3-pip python3-venv git curl openssl
```

### 2. Clone and enter the repo

```bash
sudo mkdir -p /opt/z2w
sudo chown $USER:$USER /opt/z2w
git clone <your-repo-url> /opt/z2w
cd /opt/z2w
```

### 3. Create virtual environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### 4. Configure environment

```bash
nano .env   # create from scratch or copy from a template
```

See [Configuration](#configuration) for all variables.

### 5. Generate SSL certificate

```bash
python3 generate_ssl_cert.py
```

Creates `cert.pem` and `key.pem` in `/opt/z2w`. Replace with a CA-signed cert for production.

### 6. Set admin password

```bash
python3 password_generator.py
```

Copy the generated credentials into your `.env`.

### 7. Lock down secrets

```bash
chmod 600 .env key.pem cert.pem
```

---

## Configuration

All settings live in `/opt/z2w/.env`.

### Zendesk

| Key | Example | Description |
|---|---|---|
| `ZENDESK_SUBDOMAIN` | `acme` | Subdomain only (no `.zendesk.com`) |
| `ZENDESK_EMAIL` | `admin@acme.com` | API user email |
| `ZENDESK_API_TOKEN` | `abc123…` | Zendesk Admin API token |

### Wasabi / S3

| Key | Example | Description |
|---|---|---|
| `WASABI_ACCESS_KEY` | `AKIA…` | Wasabi access key |
| `WASABI_SECRET_KEY` | `…` | Wasabi secret key |
| `WASABI_BUCKET_NAME` | `zd-attachments` | Attachment offload bucket |
| `WASABI_ENDPOINT` | `s3.ap-southeast-1.wasabisys.com` | Endpoint (no `https://`) |

### Attachment offload

| Key | Default | Description |
|---|---|---|
| `ATTACH_OFFLOAD_ENABLED` | `true` | Enable/disable the offload job |
| `ATTACH_OFFLOAD_INTERVAL_MINUTES` | `60` | How often to run |
| `ATTACH_OFFLOAD_BUCKET` | `supportmailboxattachments` | Target bucket |
| `ATTACH_OFFLOAD_ENDPOINT` | `s3.wasabisys.com` | Wasabi endpoint for attachments |
| `ATTACH_OFFLOAD_DAILY_LIMIT` | `0` | Max attachments/day (0 = unlimited) |
| `MAX_ATTACHMENTS_PER_RUN` | `0` | Max per single run (0 = unlimited) |
| `OFFLOAD_TIME` | `00:00` | Daily scheduled offload time (HH:MM) |
| `CONTINUOUS_OFFLOAD_INTERVAL` | `5` | Continuous cycle interval (minutes) |

### Ticket backup

| Key | Default | Description |
|---|---|---|
| `TICKET_BACKUP_ENABLED` | `true` | Enable/disable ticket backup job |
| `TICKET_BACKUP_INTERVAL_MINUTES` | `1440` | Run interval (1440 = daily) |
| `TICKET_BACKUP_BUCKET` | `supportmailboxtickets` | Target Wasabi bucket |
| `TICKET_BACKUP_ENDPOINT` | `s3.eu-central-1.wasabisys.com` | Endpoint for ticket backup bucket |
| `TICKET_BACKUP_DAILY_LIMIT` | `0` | Max tickets/day (0 = unlimited) |
| `TICKET_BACKUP_MAX_PER_RUN` | `0` | Max per run (0 = unlimited) |
| `TICKET_BACKUP_TIME` | `01:00` | Daily scheduled backup time (HH:MM) |

### Application

| Key | Default | Description |
|---|---|---|
| `SECRET_KEY` | *(required)* | Flask session secret — use a long random string |
| `ADMIN_USERNAME` | `admin` | Local admin login username |
| `ADMIN_PASSWORD` | *(required)* | Local admin password (set via `password_generator.py`) |
| `ADMIN_PANEL_PORT` | `5000` | HTTPS port |
| `ADMIN_PANEL_HOST` | `0.0.0.0` | Bind address |
| `SSL_CERT_PATH` | `` | Path to TLS cert (blank = use auto-generated) |
| `SSL_KEY_PATH` | `` | Path to TLS key |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

### Scheduler

| Key | Default | Description |
|---|---|---|
| `SCHEDULER_TIMEZONE` | `UTC` | Timezone for all cron jobs |
| `RECHECK_HOUR` | `2` | Hour of day for automatic recheck run |
| `STORAGE_REPORT_INTERVAL` | `60` | Minutes between storage reports |

### Notifications

| Key | Example | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | `123:ABC…` | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | `-100123456` | Target chat/channel ID |
| `SLACK_WEBHOOK_URL` | `https://hooks.slack.com/…` | Slack Incoming Webhook URL |
| `SMTP_SERVER` | `smtp.gmail.com` | SMTP server |
| `SMTP_PORT` | `587` | SMTP port |
| `SMTP_USERNAME` | `alerts@acme.com` | SMTP username |
| `SMTP_PASSWORD` | `…` | SMTP password |
| `REPORT_EMAIL` | `team@acme.com` | Recipient for email reports |

### OAuth (Microsoft Entra)

| Key | Description |
|---|---|
| `OAUTH_CLIENT_ID` | App Registration client ID |
| `OAUTH_CLIENT_SECRET` | App Registration client secret |
| `OAUTH_AUTHORITY` | Tenant authority URL (default: `https://login.microsoftonline.com/common`) |
| `OAUTH_REDIRECT_PATH` | Callback path (default: `/getAToken`) |

Allowed email domains for OAuth SSO are configured in `config.py` under `ALLOWED_DOMAINS`.

---

## Running as a Systemd Service

### Create the service file

```bash
sudo nano /etc/systemd/system/z2w.service
```

```ini
[Unit]
Description=Zendesk to Wasabi Offloader
After=network.target

[Service]
Type=simple
User=www-data
WorkingDirectory=/opt/z2w
ExecStart=/opt/z2w/.venv/bin/python /opt/z2w/main.py
Restart=always
RestartSec=10
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
```

### Enable and start

```bash
sudo chown -R www-data:www-data /opt/z2w
sudo systemctl daemon-reload
sudo systemctl enable z2w.service
sudo systemctl start z2w.service
sudo systemctl status z2w.service
```

### Useful commands

```bash
sudo systemctl restart z2w.service      # apply config/code changes
sudo systemctl stop z2w.service
journalctl -u z2w.service -f            # live systemd logs
tail -f /opt/z2w/logs/app.log.*         # app-level rotating logs
```

---

## Admin Panel

Access at `https://<server-ip>:<ADMIN_PANEL_PORT>/` (default: port 5000).

| Page | Description |
|---|---|
| **Dashboard** | Live stats: tickets processed, attachments offloaded, storage saved, inlines handled. Last 20 offload log entries. |
| **Offload Logs** | Paginated/filterable table of every run: date, tickets checked, offloaded, errors, inlines, duration, status. |
| **Processed Tickets** | Full list of tickets with offloaded attachments. Click ticket ID to open in Zendesk. |
| **Recheck Report** | Triggers a full historical scan; results stream live via progress feed. |
| **Storage Usage** | Per-bucket object count and bytes from Wasabi, fetched live. |
| **Ticket Backup** | Status and controls for the closed-ticket metadata backup job. |
| **Settings** | Live `.env` editor — no restart needed for most keys. |

---

## How It Works

### Offload cycle

1. **Fetch** — queries Zendesk Search API for recently updated `solved`/`closed` tickets
2. **Filter** — skips tickets already in `processed_tickets`
3. **Download** — streams each attachment from `attachments.zendesk.com`
4. **Upload** — stores at `<bucket>/tickets/<ticket_id>/<filename>` on Wasabi
5. **Patch** — rewrites the Zendesk comment body, replacing CDN URLs with Wasabi URLs
6. **Redact** — deletes the original Zendesk attachment to free Zendesk storage
7. **Log** — writes to `offload_logs` and `processed_tickets`; fires Telegram/Slack/email if any work was done

### Inline images

HTML comment bodies are scanned for `<img src="…attachments.zendesk.com…">` tags. Matched images are uploaded to Wasabi and `src` is patched in place. Tracked in separate `inlines_uploaded` / `inlines_deleted` counters.

### Recheck-all

Fetches the *complete* solved ticket history from Zendesk (all pages) and diffs against the local DB. Missing tickets are queued for immediate offload.

### Ticket backup

`ticket_backup_manager.py` archives closed-ticket metadata (comments, fields, tags) as JSON objects into `TICKET_BACKUP_BUCKET` on a configurable schedule.

---

## Ticket Explorer (Next.js)

The `explorer/` app is a standalone Next.js 16 frontend for browsing and inspecting offloaded tickets.

```bash
cd /opt/z2w/explorer
npm install
npm run dev      # http://localhost:3000
npm run build    # production build
npm run lint     # ESLint check
```

**Key source paths:**

| Path | Purpose |
|---|---|
| `src/app/` | App Router pages |
| `src/components/ExplorerShell.tsx` | Main shell layout |
| `src/components/panels/` | Panel components |
| `src/lib/api.ts` | API calls to the Flask backend |
| `src/lib/storage.ts` | Local state persistence |

The `uz/` app is a UI sandbox for prototyping new components using Base UI and Tailwind v4. Run identically with `npm run dev` from `uz/`.

---

## Backup System

Daily backups run automatically via the APScheduler job in `scheduler.py`.

### What is backed up

| Item | Path | Method |
|---|---|---|
| SQLite database | `tickets.db` | Hot copy (WAL checkpoint first) |
| Application logs | `logs/` | Entire directory tree |
| Configuration | `.env` | Included in archive |

### Backup storage

1. **Local**: `/opt/z2w/backups/z2w-backup-YYYY-MM-DD.tar.gz`
2. **Wasabi**: uploaded to `<WASABI_BUCKET_NAME>/backups/` (retained 30 days)

### Restore procedure

```bash
sudo systemctl stop z2w.service
cd /opt/z2w
tar -xzf backups/z2w-backup-YYYY-MM-DD.tar.gz
cp backup/tickets.db ./tickets.db
cp backup/.env ./.env
sudo systemctl start z2w.service
```

### Manual backup trigger

```bash
cd /opt/z2w
source .venv/bin/activate
python3 -c "from backup_manager import run_backup; run_backup()"
```

---

## Troubleshooting

### Service won't start

```bash
journalctl -u z2w.service -n 50 --no-pager
```

Common causes:
- Missing `.env` or required key not set
- Port conflict — change `ADMIN_PANEL_PORT` in `.env`
- SSL cert missing — run `python3 generate_ssl_cert.py`
- Wrong ownership — `sudo chown -R www-data:www-data /opt/z2w`

### No attachments being offloaded

1. Verify Zendesk credentials (`ZENDESK_SUBDOMAIN`, `ZENDESK_EMAIL`, `ZENDESK_API_TOKEN`)
2. API token must have **Admin** scope (Attachments write + Tickets write)
3. Check logs: `tail -f /opt/z2w/logs/app.log.*`
4. Tickets must be in `solved` or `closed` state
5. Check `ATTACH_OFFLOAD_ENABLED=true` in `.env`

### Wasabi upload errors

1. `WASABI_ENDPOINT` must match the bucket's region exactly
2. IAM policy needs `s3:PutObject`, `s3:DeleteObject`, `s3:ListBucket`
3. Check bucket CORS for browser access to Wasabi URLs

### OAuth login not working

1. `OAUTH_REDIRECT_PATH` callback must match the Azure App Registration redirect URI
2. Use `https://` — HTTP will be rejected by Entra
3. App Registration requires **User.Read** delegated permission with admin consent

### Database locked / errors

```bash
sqlite3 /opt/z2w/tickets.db "PRAGMA integrity_check;"
sqlite3 /opt/z2w/tickets.db "PRAGMA wal_checkpoint(TRUNCATE);"
```

---

## Security

- **HTTPS only** — Flask served with TLS; replace self-signed cert for internet-facing deployments
- **Secrets in `.env`** — never commit `.env`; file permissions must be `600`
- **Session secret** — generate with `python3 -c "import secrets; print(secrets.token_hex(32))"`
- **Admin password** — stored as a hash; regenerate via `python3 password_generator.py`
- **OAuth** — Microsoft Entra enforces MFA and conditional access upstream
- **Wasabi keys** — use a dedicated IAM user scoped to the offloader bucket only
- **Log rotation** — daily, with monthly archive; logs contain ticket IDs but not attachment content

To report a security issue, contact the maintainer directly rather than opening a public issue.

---

## Maintenance

### Clean caches and build artifacts

```bash
# Python
find /opt/z2w -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true

# Next.js
rm -rf /opt/z2w/explorer/.next /opt/z2w/explorer/out
rm -rf /opt/z2w/uz/.next
```

### Log archive

Logs are rotated daily and automatically archived under `logs/archive/`. To manually purge logs older than 90 days:

```bash
find /opt/z2w/logs/archive -name "*.log.*" -mtime +90 -delete
```

### Dependency updates

```bash
# Python
source /opt/z2w/.venv/bin/activate
pip list --outdated
pip install --upgrade -r requirements.txt

# Node (explorer)
cd /opt/z2w/explorer && npm outdated && npm update
```

---

*z2w · RHC Solutions*
