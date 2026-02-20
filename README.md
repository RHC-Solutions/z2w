# z2w — Zendesk to Wasabi Offloader

Automated system that offloads Zendesk ticket attachments and inline images to **Wasabi S3-compatible storage**, replacing the original URLs in ticket comments so Zendesk storage costs decrease over time.

---

## Table of Contents

1. [Overview](#overview)
2. [Features](#features)
3. [Architecture](#architecture)
4. [Installation (Debian/Ubuntu)](#installation-debianubuntu)
5. [Configuration](#configuration)
6. [Running as a Systemd Service](#running-as-a-systemd-service)
7. [Admin Panel](#admin-panel)
8. [How It Works](#how-it-works)
9. [Backup System](#backup-system)
10. [Troubleshooting](#troubleshooting)
11. [Security](#security)

---

## Overview

**z2w** monitors Zendesk for tickets with attachments (and inline images). Every 5 minutes it:

1. Fetches new closed/solved tickets with attachments
2. Downloads each file from Zendesk CDN
3. Uploads it to a configured Wasabi bucket
4. Updates the comment body in Zendesk to point to the Wasabi URL
5. Redacts the original Zendesk attachment

An admin panel (Flask web app) provides full visibility into processed tickets, offload logs, storage statistics, a recheck-all report, and an embedded Zendesk Explorer view.

---

## Features

| Feature | Detail |
|---|---|
| **Continuous offload** | Runs every 5 minutes via APScheduler |
| **Inline image tracking** | Counts inlines uploaded and deleted per run |
| **Recheck-all report** | Scans all solved tickets to catch any missed attachments |
| **Storage Usage page** | Per-bucket breakdown with live Wasabi size query |
| **Zendesk Explorer** | Embedded Explorer iframe with OAuth SSO pass-through |
| **Smart notifications** | Telegram / Slack / email reports sent only when work was done |
| **OAuth login** | Microsoft Entra ID (Azure AD) OIDC + local password fallback |
| **Dark mode UI** | Figtree font, oklch colour system, responsive sidebar |
| **SQLite WAL mode** | Concurrent read/write with minimal locking |
| **Per-ticket logging** | Every offload action logged to `offload_logs` + `processed_tickets` |
| **Backup system** | Daily tar.gz snapshots of database + logs, Wasabi-archived copies |
| **SSL** | Self-signed cert auto-generated on first run; swap for real cert as needed |

---

## Architecture

```
z2w/
├── main.py                # Flask app factory, routes, OAuth
├── admin_panel.py         # Flask Blueprint (dashboard, tickets, logs, settings, explorer)
├── offloader.py           # Core offload engine (download → upload → patch Zendesk)
├── scheduler.py           # APScheduler jobs (offload every 5 min, backup daily)
├── database.py            # SQLite helpers, WAL mode, migrations
├── config.py              # .env loader + defaults
├── zendesk_client.py      # Zendesk REST API wrapper
├── wasabi_client.py       # boto3/S3 wrapper for Wasabi
├── oauth_auth.py          # Microsoft OIDC flow
├── email_reporter.py      # SMTP summary emails
├── telegram_reporter.py   # Telegram Bot API summaries
├── slack_reporter.py      # Slack Incoming Webhook summaries
├── logger_config.py       # Rotating file + console logging
├── password_generator.py  # Admin password hash utility
├── templates/             # Jinja2 HTML templates (dark mode)
│   ├── base.html          # Shared layout, sidebar, CSS design system
│   ├── dashboard.html     # Stats + recent offload log
│   ├── tickets.html       # Processed tickets table (paginated)
│   ├── logs.html          # Offload logs table (paginated, filterable)
│   ├── settings.html      # Live .env editor
│   ├── login.html         # Login page (password + OAuth)
│   └── ...
├── static/                # Favicon, logo assets
├── logs/                  # Daily rotating log files
├── z2w.db                 # SQLite database (auto-created)
├── requirements.txt
└── .env                   # Runtime configuration (not in git)
```

---

## Installation (Debian/Ubuntu)

### Prerequisites

- Debian 12 / Ubuntu 22.04+ (64-bit)
- Python 3.11+
- `sudo` access
- A Zendesk admin API token
- A Wasabi account + bucket
- (Optional) Microsoft Entra App Registration for OAuth

### 1. Install system packages

```bash
sudo apt update && sudo apt install -y python3 python3-pip python3-venv git curl openssl
```

### 2. Clone the repository

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

### 4. Create `.env` configuration

```bash
cp .env.example .env   # if provided, otherwise create from scratch
nano .env
```

See [Configuration](#configuration) for all variables.

### 5. Generate SSL certificate

```bash
python3 generate_ssl_cert.py
```

This creates `cert.pem` and `key.pem` in `/opt/z2w`. Replace with a CA-signed certificate for production.

### 6. Set admin password

```bash
python3 password_generator.py
```

Copy the generated hash into your `.env` as `ADMIN_PASSWORD_HASH`.

### 7. Set permissions

```bash
chmod 600 .env key.pem cert.pem
```

---

## Configuration

All settings live in `/opt/z2w/.env`. The table below lists every recognised key.

### Zendesk

| Key | Example | Description |
|---|---|---|
| `ZENDESK_SUBDOMAIN` | `acme` | Your Zendesk subdomain (before `.zendesk.com`) |
| `ZENDESK_EMAIL` | `admin@acme.com` | API user email |
| `ZENDESK_API_TOKEN` | `abc123…` | Zendesk Admin API token |
| `ZENDESK_EXPLORER_URL` | `https://acme.zendesk.com/explore` | URL embedded in Explorer tab |

### Wasabi / S3

| Key | Example | Description |
|---|---|---|
| `WASABI_ACCESS_KEY` | `AKIA…` | Wasabi access key |
| `WASABI_SECRET_KEY` | `…` | Wasabi secret key |
| `WASABI_BUCKET_NAME` | `zd-attachments` | Bucket for attachments |
| `WASABI_REGION` | `ap-southeast-1` | Bucket region |
| `WASABI_ENDPOINT_URL` | `https://s3.ap-southeast-1.wasabisys.com` | Wasabi endpoint |

### Application

| Key | Default | Description |
|---|---|---|
| `SECRET_KEY` | *(required)* | Flask session secret — use a long random string |
| `ADMIN_PASSWORD_HASH` | *(required)* | bcrypt hash generated by `password_generator.py` |
| `PORT` | `5000` | HTTPS port |
| `HOST` | `0.0.0.0` | Bind address |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |

### Notifications

| Key | Example | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | `123:ABC…` | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | `-100123456` | Target chat/channel ID |
| `SLACK_WEBHOOK_URL` | `https://hooks.slack.com/…` | Slack Incoming Webhook URL |
| `SMTP_HOST` | `smtp.gmail.com` | SMTP server |
| `SMTP_PORT` | `587` | SMTP port |
| `SMTP_USER` | `alerts@acme.com` | SMTP username |
| `SMTP_PASSWORD` | `…` | SMTP password |
| `REPORT_EMAIL_TO` | `team@acme.com` | Recipient address for email reports |
| `STORAGE_REPORT_INTERVAL` | `24` | Hours between storage-usage reports (Telegram/Slack) |

### OAuth (Microsoft Entra)

| Key | Description |
|---|---|
| `AZURE_CLIENT_ID` | App Registration client ID |
| `AZURE_CLIENT_SECRET` | App Registration client secret |
| `AZURE_TENANT_ID` | Entra tenant ID |
| `AZURE_REDIRECT_URI` | Must match the redirect URI registered in Azure (`https://host/auth/callback`) |

---

## Running as a Systemd Service

### Create service file

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
journalctl -u z2w.service -f            # live logs
tail -f /opt/z2w/logs/app.log.*         # app-level logs
```

---

## Admin Panel

Access at `https://<server-ip>:<PORT>/` (default port 5000).

### Dashboard

Real-time stats: total tickets processed, attachments offloaded, storage saved, inline images handled. Shows the last 20 offload log entries with inline upload/delete counts.

### Offload Logs

Paginated, filterable table of every offload run. Columns: date, tickets checked, offloaded, errors, inlines uploaded/deleted, duration, status.

### Processed Tickets

Full list of every ticket that has had attachments offloaded. Click a ticket ID to open it in Zendesk.

### Recheck Report

Triggers a full scan of all solved Zendesk tickets against the local database, reporting any tickets that were missed. Results show as a live-updating progress feed.

### Storage Usage

Per-bucket breakdown of total objects and bytes stored on Wasabi, fetched live. Includes a per-ticket storage estimate chart.

### Zendesk Explorer

Embedded Zendesk Explorer analytics application via iframe, using your existing Zendesk session.

### Settings

Live `.env` editor — change any configuration value without restarting the service (most settings reload on next scheduler tick; OAuth/port changes require a restart).

---

## How It Works

### Offload cycle (every 5 minutes)

1. **Fetch** — calls Zendesk Search API for recently updated `solved`/`closed` tickets
2. **Filter** — skips tickets already in `processed_tickets` table
3. **Download** — streams each attachment from `attachments.zendesk.com`
4. **Upload** — puts the file in Wasabi at `<bucket>/tickets/<ticket_id>/<filename>`
5. **Patch** — updates the Zendesk comment body, replacing CDN URLs with Wasabi URLs
6. **Redact** — deletes the original Zendesk attachment to free Zendesk storage
7. **Log** — writes to `offload_logs` and `processed_tickets`; emits Telegram/Slack/email if work was done

### Inline images

HTML comment bodies are scanned for `<img src="…attachments.zendesk.com…">` tags. Matched images are uploaded to Wasabi and the `src` attribute is patched in place. The `inlines_uploaded` and `inlines_deleted` counts are tracked separately in the offload log.

### Recheck-all

Fetches the *complete* solved ticket history from Zendesk (all pages) and compares against the local DB. Any ticket present in Zendesk but missing from `processed_tickets` is queued for immediate offload.

---

## Backup System

Daily backups run automatically via the APScheduler job in `scheduler.py`.

### What is backed up

| Item | Path | Backup method |
|---|---|---|
| SQLite database | `z2w.db` | Hot copy (WAL checkpoint first) |
| Application logs | `logs/` | Entire directory tree |
| Configuration | `.env` | Included in archive |

### Backup storage

1. **Local**: `/opt/z2w/backups/z2w-backup-YYYY-MM-DD.tar.gz`
2. **Wasabi**: uploaded to `<WASABI_BUCKET_NAME>/backups/` and kept for 30 days

### Restore procedure

```bash
# Stop service
sudo systemctl stop z2w.service

# Extract backup
cd /opt/z2w
tar -xzf backups/z2w-backup-YYYY-MM-DD.tar.gz

# Restore specific files
cp backup/z2w.db ./z2w.db
cp backup/.env ./.env

# Restart
sudo systemctl start z2w.service
```

### Manual backup

```bash
cd /opt/z2w
source .venv/bin/activate
python3 -c "from scheduler import run_backup; run_backup()"
```

---

## Troubleshooting

### Service won't start

```bash
journalctl -u z2w.service -n 50 --no-pager
```

Common causes:
- Missing `.env` file or required key not set
- Port already in use — change `PORT` in `.env`
- SSL cert not generated — run `python3 generate_ssl_cert.py`
- Wrong file ownership — `sudo chown -R www-data:www-data /opt/z2w`

### No attachments being offloaded

1. Check Zendesk credentials: `ZENDESK_SUBDOMAIN`, `ZENDESK_EMAIL`, `ZENDESK_API_TOKEN`
2. Verify API token has **Admin** scope (Attachments write + Tickets write)
3. Check logs: `tail -f /opt/z2w/logs/app.log.*`
4. Confirm tickets are in `solved` or `closed` state

### Wasabi upload errors

1. Verify `WASABI_ENDPOINT_URL` matches the bucket's region
2. Ensure the IAM user/policy has `s3:PutObject`, `s3:DeleteObject`, `s3:ListBucket`
3. Check bucket CORS if accessing Wasabi URLs in browser

### OAuth login not working

1. Confirm `AZURE_REDIRECT_URI` exactly matches what is registered in the Azure App Registration
2. Ensure `https://` is used (HTTP will fail with Entra)
3. Check that the App Registration has **User.Read** delegated permission and admin consent granted

### Database errors / locked

```bash
sqlite3 /opt/z2w/z2w.db "PRAGMA integrity_check;"
sqlite3 /opt/z2w/z2w.db "PRAGMA wal_checkpoint(TRUNCATE);"
```

---

## Security

- **HTTPS only** — Flask is served with TLS; replace the self-signed cert for internet-facing deployments
- **Secrets in `.env`** — never commit `.env` to source control; file permissions should be `600`
- **Session secrets** — set `SECRET_KEY` to a long random string (`python3 -c "import secrets; print(secrets.token_hex(32))"`)
- **Admin password** — stored as bcrypt hash; regenerate with `python3 password_generator.py`
- **OAuth** — Microsoft Entra enforces MFA and conditional access policies upstream
- **Wasabi keys** — use a dedicated IAM user scoped to the offloader bucket only
- **Log rotation** — daily rotation with archive; logs contain ticket IDs but not attachment data

To report a security issue, contact the maintainer directly rather than opening a public issue.

---

*z2w · RHC Solutions*
