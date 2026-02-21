# z2w — Copilot Instructions

## Project Overview
**z2w** offloads Zendesk ticket attachments and inline images to Wasabi S3, rewrites the source URLs inside Zendesk comments, and redacts the originals. It ships three sub-components:

| Component | Path | Stack |
|---|---|---|
| Offloader daemon + Admin panel | `/opt/z2w/` (root) | Python 3 · Flask · APScheduler · SQLite |
| Ticket Explorer | `explorer/` | Next.js 16 · Tailwind v4 · shadcn/ui · static export |
| UX sandbox | `uz/` | Next.js 16 · Tailwind v4 · Base UI |

---

## Architecture & Data Flow

**Core pipeline** (triggered every 5 min via APScheduler):
`scheduler.py` → `offloader.py (AttachmentOffloader)` → `zendesk_client.py` → `wasabi_client.py` → writes results to `tickets.db`

**Key design decisions:**
- SQLite with WAL mode (`PRAGMA journal_mode=WAL`, `busy_timeout=30000`) and `NullPool` — every SQLAlchemy session gets its own connection to avoid write-lock contention between the Flask thread and APScheduler background jobs.
- Settings live in **two places**: `.env` file (loaded via `python-dotenv`) AND a `settings` table in SQLite. `AttachmentOffloader.__init__` calls `config.reload_config()` twice — once for `.env`, once after syncing DB settings into `os.environ`. Always call `reload_config()` when settings may have changed at runtime.
- **Multi-tenant**: `global.db` holds one row per tenant (`Tenant` + `TenantSetting` models in `tenant_manager.py`). Each tenant gets `tenants/{slug}/tickets.db`. The single-tenant legacy path still reads from root `tickets.db` via `database.py`.

**Databases:**
- `tickets.db` — main per-instance DB: `processed_tickets`, `offload_logs`, `zendesk_ticket_cache`, `zendesk_storage_snapshot`, `ticket_backup_runs`, `ticket_backup_items`, `settings`
- `global.db` — multi-tenant registry: `tenants`, `tenant_settings`

**Schema migrations** are additive, handled by `_migrate_database()` in `database.py` — uses `ALTER TABLE ADD COLUMN` and `CREATE TABLE IF NOT EXISTS` patterns, never drops columns.

---

## Developer Workflows

**Run the daemon (Debian/Ubuntu):**
```bash
# First time setup
bash setup_debian.sh

# Start (activates venv, installs deps, runs main.py)
bash run_debian.sh

# Or manually
source venv/bin/activate
python main.py
```

**Build the Explorer (static export → served by Flask at `/explorer/app/`):**
```bash
cd explorer
npm install
npm run build      # outputs to explorer/out/
# Flask serves explorer/out/ under /explorer/app via static route
```

**Explorer dev mode** (proxied Zendesk calls route through Flask backend):
```bash
cd explorer && npm run dev   # http://localhost:3000
```

**Bulk/one-shot utilities** (run standalone, not via scheduler):
```bash
python bulk_inline_offload.py     # migrate inline images in bulk
python bulk_inline_remaining.py   # resume interrupted inline migration
python bulk_reoffload.py          # re-offload specific tickets
python bulk_redact_zendesk.py     # redact already-uploaded attachments
python mass_offload.py            # high-throughput batch offload
```

---

## Configuration Conventions

- All runtime config is in `.env` (not committed). `config.py` maps every variable with safe defaults.
- Boolean env vars accept `"1"`, `"true"`, `"yes"`, `"on"` (case-insensitive).
- `0` means **unlimited** for all `*_MAX_PER_RUN` and `*_DAILY_LIMIT` settings.
- Two separate Wasabi endpoints/buckets exist: `ATTACH_OFFLOAD_*` (attachments) and `TICKET_BACKUP_*` (closed-ticket metadata). Don't conflate them.
- OAuth via Microsoft Entra ID (MSAL, `oauth_auth.py`). Domain allowlist is hardcoded in `config.py` (`ALLOWED_DOMAINS`).

---

## Critical Patterns

**Logger name:** always use `logging.getLogger('zendesk_offloader')` — never create a new logger name.

**DB session lifecycle:** always close sessions in a `finally` block:
```python
db = get_db()
try:
    ...
finally:
    db.close()
```

**Admin panel routes** are all in `admin_panel.py` (3000+ lines). API routes are prefixed `/api/` and return JSON; page routes return rendered Jinja2 templates. Error handlers detect `request.path.startswith('/api/')` to choose response format.

**Explorer ↔ Flask integration:** The Next.js Explorer is built as a static export (`output: "export"`) with `basePath: "/explorer/app"`. Zendesk API calls from the browser are proxied through a Flask route (`/explorer/api/proxy`) to avoid CORS. Credentials are fetched from `/api/explorer/settings` on load and override localStorage.

**Jinja2 templates** use a shared `base.html` (dark mode, oklch colour system, Figtree font). All templates extend it. A custom `fromjson` filter is registered on the Flask app for parsing JSON stored in DB text columns.

**Notification reporters** (`email_reporter.py`, `telegram_reporter.py`, `slack_reporter.py`) are only invoked when actual work was done — check the `report_sent` flag on `OffloadLog` to avoid duplicate sends.

**Scheduler locking:** Each APScheduler job has its own `threading.Lock` (e.g. `_continuous_lock`, `_ticket_backup_lock`) to prevent overlapping runs. Always check and set the corresponding `_*_running` flag.
