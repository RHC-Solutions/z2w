"""
Main entry point for Zendesk to Wasabi B2 Offloader
"""
from database import init_db
from admin_panel import app, init_scheduler
from config import ADMIN_PANEL_PORT, ADMIN_PANEL_HOST
from logger_config import setup_logging, archive_old_logs
import logging

if __name__ == '__main__':
    # Set up logging first
    logger = setup_logging()
    
    # Archive old logs on startup (older than 7 days)
    logger.info("Checking for old logs to archive...")
    archive_old_logs(days_to_keep=7)
    
    # Capture print statements and redirect to logger
    # Note: This will capture all print() calls, but we'll keep console output too
    # We'll use a custom approach that logs AND prints
    
    # Initialize database
    logger.info("Initializing database...")
    init_db()
    
    # Initialize and start scheduler
    logger.info("Starting scheduler...")
    scheduler = init_scheduler()
    scheduler.start()
    
    # Configure Flask logging to suppress favicon noise
    import logging
    werkzeug_log = logging.getLogger('werkzeug')
    werkzeug_log.setLevel(logging.ERROR)  # Only show errors, not access logs
    
    # Run Flask admin panel
    logger.info(f"Starting admin panel on http://{ADMIN_PANEL_HOST}:{ADMIN_PANEL_PORT}")
    app.run(host=ADMIN_PANEL_HOST, port=ADMIN_PANEL_PORT, debug=False)


