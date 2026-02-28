"""
Database models and setup
"""
import threading
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, Text, BigInteger
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool
from config import DATABASE_PATH

# Thread-local storage used by the scheduler to route get_db() to the right
# per-tenant DB without touching Flask's request context.
_thread_local = threading.local()


def set_current_tenant(slug):
    """Set the active tenant slug for the current thread.
    Pass None to clear (use root DB)."""
    _thread_local.slug = slug

Base = declarative_base()

class ProcessedTicket(Base):
    """Track processed tickets to avoid reprocessing"""
    __tablename__ = 'processed_tickets'
    
    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, unique=True, nullable=False, index=True)
    processed_at = Column(DateTime, default=datetime.utcnow)
    attachments_count = Column(Integer, default=0)
    status = Column(String(50), default='processed')
    error_message = Column(Text, nullable=True)
    wasabi_files = Column(Text, nullable=True)       # JSON array of S3 keys
    wasabi_files_size = Column(BigInteger, default=0) # total bytes of all uploaded files

class ZendeskTicketCache(Base):
    """Local cache of Zendesk ticket metadata — keeps a copy of every ticket
    so the recheck process never needs to pull all 16k+ tickets from the API.
    Updated incrementally on each daily run and full recheck."""
    __tablename__ = 'zendesk_ticket_cache'

    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, unique=True, nullable=False, index=True)
    subject = Column(Text, nullable=True)
    status = Column(String(50), nullable=True)          # open/pending/solved/closed
    created_at = Column(DateTime, nullable=True, index=True)
    updated_at = Column(DateTime, nullable=True)
    has_attachments = Column(Boolean, default=False)    # hint from ticket metadata
    comment_count = Column(Integer, nullable=True)
    requester_id = Column(Integer, nullable=True)
    assignee_id = Column(Integer, nullable=True)
    tags = Column(Text, nullable=True)                  # JSON array of strings
    cached_at = Column(DateTime, default=datetime.utcnow)  # when we last synced this row

class OffloadLog(Base):
    """Log all offload operations"""
    __tablename__ = 'offload_logs'
    
    id = Column(Integer, primary_key=True)
    run_date = Column(DateTime, default=datetime.utcnow, index=True)
    tickets_processed = Column(Integer, default=0)
    attachments_uploaded = Column(Integer, default=0)
    inlines_uploaded = Column(Integer, default=0)
    errors_count = Column(Integer, default=0)
    status = Column(String(50), default='completed')
    report_sent = Column(Boolean, default=False)
    details = Column(Text, nullable=True)

class ZendeskStorageSnapshot(Base):
    """Per-ticket storage snapshot pulled from Zendesk — refreshed on a configurable schedule.
    Tracks how much storage each ticket is consuming in Zendesk (attachments + inline images)."""
    __tablename__ = 'zendesk_storage_snapshot'

    id           = Column(Integer, primary_key=True)
    ticket_id    = Column(Integer, unique=True, nullable=False, index=True)
    subject      = Column(Text, nullable=True)
    zd_status    = Column(String(50), nullable=True)   # open/pending/solved/closed
    attach_count = Column(Integer, default=0)          # regular attachments still in Zendesk
    inline_count = Column(Integer, default=0)          # inline images still in Zendesk
    total_size   = Column(BigInteger, default=0)       # bytes currently in Zendesk
    last_seen_at = Column(DateTime, nullable=True)     # last time Zendesk returned this ticket
    updated_at   = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class Setting(Base):
    """Application settings"""
    __tablename__ = 'settings'
    
    id = Column(Integer, primary_key=True)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class TicketBackupRun(Base):
    """Track each closed-ticket backup scheduler run"""
    __tablename__ = 'ticket_backup_runs'

    id = Column(Integer, primary_key=True)
    run_date = Column(DateTime, default=datetime.utcnow, index=True)
    tickets_scanned = Column(Integer, default=0)
    tickets_backed_up = Column(Integer, default=0)
    files_uploaded = Column(Integer, default=0)
    bytes_uploaded = Column(BigInteger, default=0)
    errors_count = Column(Integer, default=0)
    status = Column(String(50), default='completed')
    details = Column(Text, nullable=True)

class TicketBackupItem(Base):
    """Per-ticket closed-ticket backup status for search/filter/reporting"""
    __tablename__ = 'ticket_backup_items'

    id = Column(Integer, primary_key=True)
    ticket_id = Column(Integer, unique=True, nullable=False, index=True)
    closed_at = Column(DateTime, nullable=True)
    last_backup_at = Column(DateTime, nullable=True, index=True)
    backup_status = Column(String(50), default='pending', index=True)  # pending/success/failed/skipped
    s3_prefix = Column(Text, nullable=True)
    files_count = Column(Integer, default=0)
    total_bytes = Column(BigInteger, default=0)
    last_error = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# Database setup
# NullPool: every Session gets its own connection that is immediately closed when
# the session closes — no pooled connections sitting idle and holding read locks
# while the scheduler thread tries to write.
from sqlalchemy import event as _sa_event

engine = create_engine(
    f'sqlite:///{DATABASE_PATH}',
    echo=False,
    poolclass=NullPool,
    connect_args={"check_same_thread": False, "timeout": 30},
)

@_sa_event.listens_for(engine, "connect")
def _set_sqlite_pragmas(dbapi_connection, connection_record):
    cursor = dbapi_connection.cursor()
    # WAL mode: readers never block writers, writers never block readers
    cursor.execute("PRAGMA journal_mode=WAL")
    # busy_timeout: retry locked writes for up to 30 s before raising OperationalError
    cursor.execute("PRAGMA busy_timeout=30000")
    cursor.execute("PRAGMA synchronous=NORMAL")
    # Keep WAL file small; checkpoint after every 200 pages
    cursor.execute("PRAGMA wal_autocheckpoint=200")
    cursor.close()

SessionLocal = sessionmaker(bind=engine)

def init_db():
    """Initialize database tables"""
    Base.metadata.create_all(engine)
    # Add missing columns to existing tables if needed
    _migrate_database()


def _migrate_on_engine(eng):
    """Run the same migrations against any engine (used for per-tenant DBs)."""
    _migrate_database(eng)


def _migrate_database(eng=None):
    """Add missing columns / tables to existing database"""
    if eng is None:
        eng = engine
    from sqlalchemy import inspect, text
    inspector = inspect(eng)

    # ── processed_tickets: add missing columns ───────────────────────
    if 'processed_tickets' in inspector.get_table_names():
        existing_columns = [col['name'] for col in inspector.get_columns('processed_tickets')]
        if 'wasabi_files' not in existing_columns:
            try:
                with eng.connect() as conn:
                    conn.execute(text("ALTER TABLE processed_tickets ADD COLUMN wasabi_files TEXT"))
                    conn.commit()
                print("Added wasabi_files column to processed_tickets table")
            except Exception as e:
                print(f"Note: Could not add wasabi_files column: {e}")
        if 'wasabi_files_size' not in existing_columns:
            try:
                with eng.connect() as conn:
                    conn.execute(text("ALTER TABLE processed_tickets ADD COLUMN wasabi_files_size INTEGER DEFAULT 0"))
                    conn.commit()
                print("Added wasabi_files_size column to processed_tickets table")
            except Exception as e:
                print(f"Note: Could not add wasabi_files_size column: {e}")

    # ── zendesk_ticket_cache: create if missing ────────────────────────
    if 'zendesk_ticket_cache' not in inspector.get_table_names():
        try:
            ZendeskTicketCache.__table__.create(eng)
            print("Created zendesk_ticket_cache table")
        except Exception as e:
            print(f"Note: Could not create zendesk_ticket_cache table: {e}")

    # ── zendesk_storage_snapshot: create if missing ────────────────────────
    if 'zendesk_storage_snapshot' not in inspector.get_table_names():
        try:
            ZendeskStorageSnapshot.__table__.create(eng)
            print("Created zendesk_storage_snapshot table")
        except Exception as e:
            print(f"Note: Could not create zendesk_storage_snapshot table: {e}")

    # ── ticket_backup_runs: create if missing ───────────────────────────────
    if 'ticket_backup_runs' not in inspector.get_table_names():
        try:
            TicketBackupRun.__table__.create(eng)
            print("Created ticket_backup_runs table")
        except Exception as e:
            print(f"Note: Could not create ticket_backup_runs table: {e}")

    # ── ticket_backup_items: create if missing ──────────────────────────────
    if 'ticket_backup_items' not in inspector.get_table_names():
        try:
            TicketBackupItem.__table__.create(eng)
            print("Created ticket_backup_items table")
        except Exception as e:
            print(f"Note: Could not create ticket_backup_items table: {e}")

def get_db(slug: str = None):
    """
    Get a database session.

    If *slug* is given (or a tenant slug is stored on the current Flask request
    via Flask's 'g' object, or set via set_current_tenant()), returns a session
    for that tenant's tickets.db.
    Falls back to the legacy tickets.db for backward-compat during transition.
    """
    if slug is None:
        # 1. Thread-local (scheduler / background jobs)
        slug = getattr(_thread_local, 'slug', None)
    if slug is None:
        # 2. Flask request context
        try:
            from flask import g as _g
            slug = getattr(_g, 'tenant_slug', None)
        except RuntimeError:
            # Outside of request context (scheduler, CLI)
            pass

    if slug:
        try:
            from tenant_manager import get_tenant_db_session
            return get_tenant_db_session(slug)
        except Exception:
            pass  # Fall through to legacy DB

    return SessionLocal()


def upsert_processed_ticket(db, ticket_id: int, _max_retries: int = 5, **kwargs):
    """
    Atomic upsert for processed_tickets with robust retry logic.
    Handles 'database is locked' by retrying with exponential backoff.

    kwargs are the column values: attachments_count, status, error_message,
    wasabi_files, wasabi_files_size, inlines_uploaded, inlines_deleted.
    Any key not provided keeps its existing value (via SELECT + merge before INSERT).
    """
    import time as _time

    for attempt in range(1, _max_retries + 1):
        try:
            existing = db.query(ProcessedTicket).filter_by(ticket_id=ticket_id).first()

            if existing:
                for key, val in kwargs.items():
                    if hasattr(existing, key):
                        setattr(existing, key, val)
                existing.processed_at = kwargs.get('processed_at', datetime.utcnow())
            else:
                row = ProcessedTicket(
                    ticket_id=ticket_id,
                    processed_at=kwargs.get('processed_at', datetime.utcnow()),
                    attachments_count=kwargs.get('attachments_count', 0),
                    status=kwargs.get('status', 'processed'),
                    error_message=kwargs.get('error_message', None),
                    wasabi_files=kwargs.get('wasabi_files', None),
                    wasabi_files_size=kwargs.get('wasabi_files_size', 0),
                )
                db.add(row)

            db.commit()
            return  # success
        except Exception as e:
            db.rollback()
            err_str = str(e).lower()
            if 'locked' in err_str or 'busy' in err_str:
                if attempt < _max_retries:
                    wait = 0.5 * (2 ** (attempt - 1))  # 0.5, 1, 2, 4, 8 seconds
                    import logging
                    logging.getLogger('zendesk_offloader').warning(
                        f"[upsert] DB locked for ticket {ticket_id}, retry {attempt}/{_max_retries} in {wait:.1f}s"
                    )
                    _time.sleep(wait)
                    db.expire_all()
                    continue
            # Non-lock error or last retry — try last-writer-wins fallback
            db.expire_all()
            try:
                existing = db.query(ProcessedTicket).filter_by(ticket_id=ticket_id).first()
                if existing:
                    for key, val in kwargs.items():
                        if hasattr(existing, key):
                            setattr(existing, key, val)
                    existing.processed_at = kwargs.get('processed_at', datetime.utcnow())
                    db.commit()
                    return
                else:
                    raise
            except Exception:
                db.rollback()
                if attempt >= _max_retries:
                    raise


