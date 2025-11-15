"""
Database models and setup
"""
from datetime import datetime
from sqlalchemy import create_engine, Column, Integer, String, DateTime, Boolean, Text
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from config import DATABASE_PATH

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
    wasabi_files = Column(Text, nullable=True)  # JSON array of S3 keys

class OffloadLog(Base):
    """Log all offload operations"""
    __tablename__ = 'offload_logs'
    
    id = Column(Integer, primary_key=True)
    run_date = Column(DateTime, default=datetime.utcnow, index=True)
    tickets_processed = Column(Integer, default=0)
    attachments_uploaded = Column(Integer, default=0)
    errors_count = Column(Integer, default=0)
    status = Column(String(50), default='completed')
    report_sent = Column(Boolean, default=False)
    details = Column(Text, nullable=True)

class Setting(Base):
    """Application settings"""
    __tablename__ = 'settings'
    
    id = Column(Integer, primary_key=True)
    key = Column(String(100), unique=True, nullable=False)
    value = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# Database setup
engine = create_engine(f'sqlite:///{DATABASE_PATH}', echo=False)
SessionLocal = sessionmaker(bind=engine)

def init_db():
    """Initialize database tables"""
    Base.metadata.create_all(engine)
    # Add missing columns to existing tables if needed
    _migrate_database()

def _migrate_database():
    """Add missing columns to existing database tables"""
    from sqlalchemy import inspect, text
    inspector = inspect(engine)
    
    # Check if processed_tickets table exists
    if 'processed_tickets' in inspector.get_table_names():
        # Get existing columns
        existing_columns = [col['name'] for col in inspector.get_columns('processed_tickets')]
        
        # Add wasabi_files column if it doesn't exist
        if 'wasabi_files' not in existing_columns:
            try:
                with engine.connect() as conn:
                    conn.execute(text("ALTER TABLE processed_tickets ADD COLUMN wasabi_files TEXT"))
                    conn.commit()
                print("Added wasabi_files column to processed_tickets table")
            except Exception as e:
                # Column might already exist or other error
                print(f"Note: Could not add wasabi_files column: {e}")

def get_db():
    """Get database session"""
    return SessionLocal()


