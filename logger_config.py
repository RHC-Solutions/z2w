"""
Logging configuration with daily log files and automatic archiving
"""
import logging
import sys
from pathlib import Path
from datetime import datetime, timedelta
import shutil
from logging.handlers import TimedRotatingFileHandler
import os

# Base directory for logs
BASE_DIR = Path(__file__).parent
LOGS_DIR = BASE_DIR / "logs"
ARCHIVE_DIR = BASE_DIR / "logs" / "archive"

# Create directories if they don't exist
LOGS_DIR.mkdir(exist_ok=True)
ARCHIVE_DIR.mkdir(exist_ok=True)

class ConsoleToLogHandler(logging.StreamHandler):
    """Handler that captures print statements and redirects to logger"""
    def __init__(self, logger):
        super().__init__()
        self.logger = logger
    
    def emit(self, record):
        # Format the record and send to logger
        msg = self.format(record)
        self.logger.handle(record)

class TeeOutput:
    """Class to tee output to both console and log file"""
    def __init__(self, *files):
        self.files = files
    
    def write(self, obj):
        for f in self.files:
            f.write(obj)
            f.flush()
    
    def flush(self):
        for f in self.files:
            f.flush()

def setup_logging():
    """
    Set up logging with daily rotation and console capture
    Returns the configured logger
    """
    # Create logger
    logger = logging.getLogger('zendesk_offloader')
    logger.setLevel(logging.INFO)
    
    # Remove existing handlers to avoid duplicates
    logger.handlers.clear()
    
    # Create formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    
    # Use TimedRotatingFileHandler for daily rotation at midnight
    # This automatically creates new files daily and names them with date suffix
    file_handler = TimedRotatingFileHandler(
        filename=str(LOGS_DIR / "app.log"),
        when='midnight',
        interval=1,
        backupCount=0,  # We'll handle archiving manually
        encoding='utf-8'
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    
    # Console handler (for immediate output)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)
    
    # Also set up werkzeug (Flask) logger
    werkzeug_logger = logging.getLogger('werkzeug')
    werkzeug_logger.setLevel(logging.WARNING)  # Only log warnings and errors from Flask
    
    return logger

def archive_old_logs(days_to_keep=7):
    """
    Archive log files older than specified days
    Moves them to archive directory
    """
    logger = logging.getLogger('zendesk_offloader')
    cutoff_date = datetime.now() - timedelta(days=days_to_keep)
    archived_count = 0
    
    try:
        # Process log files in logs directory
        # TimedRotatingFileHandler creates files like: app.log.2024-01-15
        # Also check for manually named files like: app_2024-01-15.log
        for log_file in LOGS_DIR.glob("app*.log*"):
            if log_file.is_file() and log_file.name != "app.log":  # Skip current active log
                # Extract date from filename or use file modification time
                file_date = None
                try:
                    # Try to extract date from filename
                    # Format 1: app.log.2024-01-15 (TimedRotatingFileHandler default format)
                    # The handler adds date as extension: app.log.YYYY-MM-DD
                    if log_file.name.startswith('app.log.'):
                        date_str = log_file.name.replace('app.log.', '')
                        if len(date_str) == 10 and date_str.count('-') == 2:  # YYYY-MM-DD format
                            file_date = datetime.strptime(date_str, '%Y-%m-%d')
                    # Format 2: app_2024-01-15.log
                    elif '_' in log_file.name and log_file.name.endswith('.log'):
                        parts = log_file.stem.split('_')
                        if len(parts) > 1:
                            date_str = parts[-1]
                            if len(date_str) == 10:  # YYYY-MM-DD format
                                file_date = datetime.strptime(date_str, '%Y-%m-%d')
                except (ValueError, IndexError):
                    pass
                
                # Fallback to file modification time if date not found in filename
                if file_date is None:
                    file_date = datetime.fromtimestamp(log_file.stat().st_mtime)
                
                # Check if file is older than cutoff
                if file_date < cutoff_date:
                    # Create archive subdirectory by year-month
                    archive_subdir = ARCHIVE_DIR / file_date.strftime('%Y-%m')
                    archive_subdir.mkdir(exist_ok=True)
                    
                    # Move file to archive
                    archive_path = archive_subdir / log_file.name
                    shutil.move(str(log_file), str(archive_path))
                    archived_count += 1
                    logger.info(f"Archived log file: {log_file.name} -> {archive_path}")
                    print(f"Archived log file: {log_file.name} -> {archive_path}")
        
        # Also check the main app.log file (if it's old enough)
        main_log = LOGS_DIR / "app.log"
        if main_log.exists():
            file_date = datetime.fromtimestamp(main_log.stat().st_mtime)
            if file_date < cutoff_date:
                archive_subdir = ARCHIVE_DIR / file_date.strftime('%Y-%m')
                archive_subdir.mkdir(exist_ok=True)
                archive_path = archive_subdir / f"app_{file_date.strftime('%Y-%m-%d')}.log"
                shutil.move(str(main_log), str(archive_path))
                archived_count += 1
                logger.info(f"Archived log file: app.log -> {archive_path}")
                print(f"Archived log file: app.log -> {archive_path}")
        
        if archived_count > 0:
            msg = f"Archived {archived_count} log file(s) older than {days_to_keep} days"
            logger.info(msg)
            print(msg)
        else:
            msg = f"No log files to archive (keeping last {days_to_keep} days)"
            logger.info(msg)
            print(msg)
            
    except Exception as e:
        error_msg = f"Error archiving logs: {e}"
        logger.error(error_msg)
        print(error_msg)

def capture_print_statements(logger):
    """
    Redirect print statements to logger
    This captures all print() calls and logs them
    """
    class PrintLogger:
        def __init__(self, logger, level=logging.INFO):
            self.logger = logger
            self.level = level
        
        def write(self, message):
            if message.strip():  # Don't log empty lines
                self.logger.log(self.level, message.rstrip())
        
        def flush(self):
            pass
    
    # Replace stdout and stderr with our logger
    sys.stdout = PrintLogger(logger, logging.INFO)
    sys.stderr = PrintLogger(logger, logging.ERROR)

