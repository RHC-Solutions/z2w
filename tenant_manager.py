"""
Multi-tenant management for z2w.

Architecture
------------
- /opt/z2w/global.db          — one row per tenant + per-tenant settings
- /opt/z2w/tenants/{slug}/    — per-tenant data directory
  - tickets.db                — the tenant's SQLAlchemy database
  - logs/                     — per-tenant log files (symlinked into /opt/z2w/logs/{slug}/)

TenantConfig is a plain dataclass that holds every setting for one tenant.
It is the single object passed to all service classes (WasabiClient, ZendeskClient, etc.)
instead of reading globals from config.py.
"""

from __future__ import annotations

import os
import re
import shutil
import logging
from dataclasses import dataclass, field, fields
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any

from sqlalchemy import (
    create_engine, Column, Integer, String, Boolean, DateTime, Text, event as sa_event,
    text as sa_text,
)
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

logger = logging.getLogger('zendesk_offloader')

BASE_DIR = Path(__file__).parent
GLOBAL_DB_PATH = BASE_DIR / 'global.db'
TENANTS_DIR = BASE_DIR / 'tenants'

# ── Global DB models ────────────────────────────────────────────────────────

GlobalBase = declarative_base()

class Tenant(GlobalBase):
    """One row per tenant."""
    __tablename__ = 'tenants'

    id          = Column(Integer, primary_key=True)
    slug        = Column(String(100), unique=True, nullable=False, index=True)  # = zendesk subdomain
    display_name = Column(String(200), nullable=True)
    color       = Column(String(20), nullable=True)   # e.g. '#14b8a6'
    is_active   = Column(Boolean, default=True, nullable=False)
    created_at  = Column(DateTime, default=datetime.utcnow)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def __repr__(self):
        return f"<Tenant slug={self.slug!r} active={self.is_active}>"


class TenantSetting(GlobalBase):
    """Key-value settings per tenant — all service configuration lives here."""
    __tablename__ = 'tenant_settings'

    id         = Column(Integer, primary_key=True)
    tenant_id  = Column(Integer, nullable=False, index=True)
    key        = Column(String(200), nullable=False)
    value      = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


# ── GlobalDB engine ─────────────────────────────────────────────────────────

_global_engine = None
_GlobalSession = None


def _get_global_engine():
    global _global_engine, _GlobalSession
    if _global_engine is None:
        _global_engine = create_engine(
            f'sqlite:///{GLOBAL_DB_PATH}',
            echo=False,
            poolclass=NullPool,
            connect_args={'check_same_thread': False, 'timeout': 30},
        )

        @sa_event.listens_for(_global_engine, 'connect')
        def _pragmas(conn, _rec):
            cur = conn.cursor()
            cur.execute('PRAGMA journal_mode=WAL')
            cur.execute('PRAGMA busy_timeout=30000')
            cur.execute('PRAGMA synchronous=NORMAL')
            cur.close()

        GlobalBase.metadata.create_all(_global_engine)
        # Additive migration: add color column if not present
        try:
            with _global_engine.connect() as _c:
                _c.execute(sa_text('ALTER TABLE tenants ADD COLUMN color VARCHAR(20)'))
                _c.commit()
        except Exception:
            pass  # column already exists
        _GlobalSession = sessionmaker(bind=_global_engine)
    return _global_engine


def get_global_db():
    """Return a new global DB session."""
    _get_global_engine()
    return _GlobalSession()


# ── TenantConfig dataclass ──────────────────────────────────────────────────

@dataclass
class TenantConfig:
    """All runtime configuration for a single tenant.
    Passed to every service class instead of reading from module-level globals."""

    slug: str = ''
    display_name: str = ''
    color: str = ''   # hex color for UI, e.g. '#14b8a6'

    # Zendesk
    zendesk_subdomain: str = ''
    zendesk_email: str = ''
    zendesk_api_token: str = ''

    # Wasabi – offload bucket (attachments / inline images)
    wasabi_endpoint: str = ''
    wasabi_access_key: str = ''
    wasabi_secret_key: str = ''
    wasabi_bucket_name: str = ''

    # Wasabi – ticket-backup bucket
    ticket_backup_endpoint: str = ''
    ticket_backup_bucket: str = ''

    # Telegram
    telegram_bot_token: str = ''
    telegram_chat_id: str = ''

    # Slack
    slack_webhook_url: str = ''
    slack_bot_token: str = ''

    # Alert preferences
    alert_on_offload_error: bool = True      # immediate alert when offload job crashes
    alert_on_backup_error: bool = True       # immediate alert when backup job crashes
    alert_daily_report: bool = True          # send daily stats at 00:01
    alert_daily_telegram: bool = True        # daily report → Telegram
    alert_daily_slack: bool = True           # daily report → Slack
    alert_include_offload_stats: bool = True # include offload section in daily report
    alert_include_backup_stats: bool = True  # include backup section in daily report
    alert_include_errors_detail: bool = True # include error detail lines in daily report

    # Scheduler / offload settings (per-tenant overrides)
    full_offload_interval: int = 5
    continuous_offload_interval: int = 5
    scheduler_timezone: str = 'UTC'
    attach_offload_enabled: bool = True
    attach_offload_interval_minutes: int = 60
    ticket_backup_enabled: bool = True
    ticket_backup_interval_minutes: int = 1440
    ticket_backup_time: str = '01:00'
    ticket_backup_max_per_run: int = 0
    max_attachments_per_run: int = 0
    storage_report_interval: int = 60

    # Per-tenant DB path (set automatically)
    db_path: str = ''

    @property
    def is_configured(self) -> bool:
        """True if the minimum required fields are present."""
        return bool(
            self.zendesk_subdomain
            and self.zendesk_api_token
            and self.wasabi_access_key
            and self.wasabi_secret_key
            and self.wasabi_bucket_name
        )

    def to_dict(self) -> Dict[str, Any]:
        return {f.name: getattr(self, f.name) for f in fields(self)}

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> 'TenantConfig':
        valid = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in valid})


# ── CRUD helpers ────────────────────────────────────────────────────────────

_SETTING_KEYS = [f.name for f in fields(TenantConfig)
                 if f.name not in ('slug', 'display_name', 'db_path')]


def _slug_valid(slug: str) -> bool:
    return bool(re.match(r'^[a-z0-9][a-z0-9\-]{0,98}[a-z0-9]$|^[a-z0-9]$', slug))


def _tenant_dir(slug: str) -> Path:
    return TENANTS_DIR / slug


def get_tenant_config(slug: str) -> Optional[TenantConfig]:
    """Load a TenantConfig from global.db.  Returns None if not found."""
    db = get_global_db()
    try:
        tenant = db.query(Tenant).filter_by(slug=slug).first()
        if not tenant:
            return None
        settings = {
            row.key: row.value
            for row in db.query(TenantSetting).filter_by(tenant_id=tenant.id).all()
        }
        cfg = TenantConfig(
            slug=tenant.slug,
            display_name=tenant.display_name or tenant.slug,
            color=tenant.color or '',
        )
        for key in _SETTING_KEYS:
            raw = settings.get(key)
            if raw is None:
                continue
            target_type = type(getattr(cfg, key))
            if target_type == bool:
                setattr(cfg, key, raw.lower() in ('1', 'true', 'yes', 'on'))
            elif target_type == int:
                try:
                    setattr(cfg, key, int(raw))
                except ValueError:
                    pass
            else:
                setattr(cfg, key, raw)
        cfg.db_path = str(_tenant_dir(slug) / 'tickets.db')
        return cfg
    finally:
        db.close()


def save_tenant_config(cfg: TenantConfig, create_if_missing: bool = True) -> bool:
    """Persist a TenantConfig to global.db.  Creates the tenant row if needed."""
    db = get_global_db()
    try:
        tenant = db.query(Tenant).filter_by(slug=cfg.slug).first()
        if not tenant:
            if not create_if_missing:
                return False
            if not _slug_valid(cfg.slug):
                raise ValueError(f"Invalid slug: {cfg.slug!r}")
            tenant = Tenant(slug=cfg.slug, display_name=cfg.display_name or cfg.slug)
            db.add(tenant)
            db.commit()
            db.refresh(tenant)
            _ensure_tenant_dirs(cfg.slug)

        tenant.display_name = cfg.display_name or tenant.display_name
        if cfg.color is not None:
            tenant.color = cfg.color
        tenant.updated_at = datetime.utcnow()
        db.commit()

        # Upsert settings
        existing = {
            row.key: row
            for row in db.query(TenantSetting).filter_by(tenant_id=tenant.id).all()
        }
        for key in _SETTING_KEYS:
            val = getattr(cfg, key)
            str_val = str(val) if not isinstance(val, bool) else ('true' if val else 'false')
            if key in existing:
                existing[key].value = str_val
                existing[key].updated_at = datetime.utcnow()
            else:
                db.add(TenantSetting(tenant_id=tenant.id, key=key, value=str_val))
        db.commit()
        return True
    except Exception as exc:
        db.rollback()
        logger.error(f'save_tenant_config({cfg.slug}): {exc}', exc_info=True)
        raise
    finally:
        db.close()


def list_tenants(active_only: bool = False) -> list[Tenant]:
    db = get_global_db()
    try:
        q = db.query(Tenant)
        if active_only:
            q = q.filter_by(is_active=True)
        return q.order_by(Tenant.created_at).all()
    finally:
        db.close()


def delete_tenant(slug: str, remove_data: bool = False) -> bool:
    """Mark tenant as inactive (soft delete) or hard-delete."""
    db = get_global_db()
    try:
        tenant = db.query(Tenant).filter_by(slug=slug).first()
        if not tenant:
            return False
        if remove_data:
            db.query(TenantSetting).filter_by(tenant_id=tenant.id).delete()
            db.delete(tenant)
            db.commit()
            tdir = _tenant_dir(slug)
            if tdir.exists():
                shutil.rmtree(tdir, ignore_errors=True)
        else:
            tenant.is_active = False
            db.commit()
        return True
    except Exception as exc:
        db.rollback()
        logger.error(f'delete_tenant({slug}): {exc}', exc_info=True)
        return False
    finally:
        db.close()


# ── Directory helpers ────────────────────────────────────────────────────────

def _ensure_tenant_dirs(slug: str):
    """Create tenants/{slug}/ and logs/{slug}/ directories."""
    tdir = _tenant_dir(slug)
    tdir.mkdir(parents=True, exist_ok=True)
    logs_dir = BASE_DIR / 'logs' / slug
    logs_dir.mkdir(parents=True, exist_ok=True)
    logger.info(f'Tenant dirs ensured: {tdir}  {logs_dir}')


# ── Per-tenant SQLAlchemy engine cache ───────────────────────────────────────

_tenant_engines: Dict[str, Any] = {}
_tenant_sessions: Dict[str, Any] = {}


def get_tenant_db_session(slug: str):
    """Return a new SQLAlchemy session for tenant *slug*'s tickets.db."""
    if slug not in _tenant_engines:
        tdir = _tenant_dir(slug)
        tdir.mkdir(parents=True, exist_ok=True)
        db_path = tdir / 'tickets.db'
        engine = create_engine(
            f'sqlite:///{db_path}',
            echo=False,
            poolclass=NullPool,
            connect_args={'check_same_thread': False, 'timeout': 30},
        )

        @sa_event.listens_for(engine, 'connect')
        def _pragmas(conn, _rec):
            cur = conn.cursor()
            cur.execute('PRAGMA journal_mode=WAL')
            cur.execute('PRAGMA busy_timeout=30000')
            cur.execute('PRAGMA synchronous=NORMAL')
            cur.execute('PRAGMA wal_autocheckpoint=200')
            cur.close()

        # Import here to avoid circular imports
        from database import Base, _migrate_on_engine
        Base.metadata.create_all(engine)
        _migrate_on_engine(engine)

        _tenant_engines[slug] = engine
        _tenant_sessions[slug] = sessionmaker(bind=engine)

    return _tenant_sessions[slug]()


def invalidate_tenant_engine(slug: str):
    """Force re-creation of the engine on next access (e.g. after DB move)."""
    _tenant_engines.pop(slug, None)
    _tenant_sessions.pop(slug, None)


# ── Startup: auto-migrate first tenant from .env ────────────────────────────

def bootstrap_first_tenant() -> Optional[str]:
    """
    If global.db has no tenants yet, create the first one from the current
    .env / config.py values and move tickets.db into tenants/{slug}/tickets.db.

    Returns the slug of the first tenant (whether it already existed or was just
    created), or None on failure.
    """
    _get_global_engine()   # ensure global.db is initialised

    existing = list_tenants()
    if existing:
        # Already bootstrapped — just make sure dirs exist
        for t in existing:
            _ensure_tenant_dirs(t.slug)
        return existing[0].slug

    # Build config from environment
    import config as _cfg
    slug = (_cfg.ZENDESK_SUBDOMAIN or 'default').lower().strip()
    slug = re.sub(r'[^a-z0-9\-]', '-', slug)
    slug = re.sub(r'-+', '-', slug).strip('-') or 'default'

    logger.info(f'bootstrap_first_tenant: creating slug={slug!r}')

    cfg = TenantConfig(
        slug=slug,
        display_name=slug,
        zendesk_subdomain=_cfg.ZENDESK_SUBDOMAIN,
        zendesk_email=_cfg.ZENDESK_EMAIL,
        zendesk_api_token=_cfg.ZENDESK_API_TOKEN,
        wasabi_endpoint=_cfg.WASABI_ENDPOINT,
        wasabi_access_key=_cfg.WASABI_ACCESS_KEY,
        wasabi_secret_key=_cfg.WASABI_SECRET_KEY,
        wasabi_bucket_name=_cfg.WASABI_BUCKET_NAME,
        ticket_backup_endpoint=_cfg.TICKET_BACKUP_ENDPOINT,
        ticket_backup_bucket=_cfg.TICKET_BACKUP_BUCKET,
        telegram_bot_token=_cfg.TELEGRAM_BOT_TOKEN,
        telegram_chat_id=_cfg.TELEGRAM_CHAT_ID,
        slack_webhook_url=_cfg.SLACK_WEBHOOK_URL,
        continuous_offload_interval=_cfg.CONTINUOUS_OFFLOAD_INTERVAL,
        scheduler_timezone=_cfg.SCHEDULER_TIMEZONE,
        attach_offload_enabled=_cfg.ATTACH_OFFLOAD_ENABLED,
        attach_offload_interval_minutes=_cfg.ATTACH_OFFLOAD_INTERVAL_MINUTES,
        ticket_backup_enabled=_cfg.TICKET_BACKUP_ENABLED,
        ticket_backup_interval_minutes=_cfg.TICKET_BACKUP_INTERVAL_MINUTES,
        ticket_backup_time=_cfg.TICKET_BACKUP_TIME,
        ticket_backup_max_per_run=_cfg.TICKET_BACKUP_MAX_PER_RUN,
        max_attachments_per_run=_cfg.MAX_ATTACHMENTS_PER_RUN,
        storage_report_interval=_cfg.STORAGE_REPORT_INTERVAL,
    )
    save_tenant_config(cfg)

    # Move existing tickets.db into the tenant directory
    legacy_db = BASE_DIR / 'tickets.db'
    tenant_db = _tenant_dir(slug) / 'tickets.db'
    if legacy_db.exists() and not tenant_db.exists():
        shutil.copy2(str(legacy_db), str(tenant_db))
        logger.info(f'Copied {legacy_db} → {tenant_db}')
        # Keep the original as tickets.db.bak so nothing breaks during transition
        legacy_bak = BASE_DIR / 'tickets.db.bak'
        if not legacy_bak.exists():
            shutil.copy2(str(legacy_db), str(legacy_bak))

    return slug
