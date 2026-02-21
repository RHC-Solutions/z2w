"""
Main entry point for Zendesk to Wasabi B2 Offloader
"""
from database import init_db
from admin_panel import app, init_scheduler
from config import ADMIN_PANEL_PORT, ADMIN_PANEL_HOST, SSL_CERT_PATH, SSL_KEY_PATH
from logger_config import setup_logging, archive_old_logs
import logging
import ssl
import os

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

    # Bootstrap multi-tenant global.db (creates first tenant from .env if needed)
    try:
        from tenant_manager import bootstrap_first_tenant
        first_slug = bootstrap_first_tenant()
        logger.info(f"Multi-tenant ready — first tenant: {first_slug}")
    except Exception as _e:
        logger.warning(f"bootstrap_first_tenant failed (non-fatal): {_e}")
    
    # Initialize and start scheduler
    logger.info("Starting scheduler...")
    scheduler = init_scheduler()
    scheduler.start()
    
    # Configure Flask logging to suppress favicon noise
    import logging
    werkzeug_log = logging.getLogger('werkzeug')
    werkzeug_log.setLevel(logging.ERROR)  # Only show errors, not access logs
    
    # Configure SSL context if certificates are provided
    ssl_context = None
    protocol = "http"
    if SSL_CERT_PATH and SSL_KEY_PATH:
        cert_path = os.path.abspath(SSL_CERT_PATH)
        key_path = os.path.abspath(SSL_KEY_PATH)
        
        if os.path.exists(cert_path) and os.path.exists(key_path):
            try:
                ssl_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
                ssl_context.load_cert_chain(cert_path, key_path)
                protocol = "https"
                logger.info(f"SSL certificates loaded: cert={cert_path}, key={key_path}")
            except Exception as e:
                logger.error(f"Failed to load SSL certificates: {e}")
                logger.warning("Falling back to HTTP")
        else:
            logger.warning(f"SSL certificate files not found. Cert: {cert_path}, Key: {key_path}")
            logger.warning("Falling back to HTTP")
    
    # Run Flask admin panel
    logger.info(f"Starting admin panel on {protocol}://{ADMIN_PANEL_HOST}:{ADMIN_PANEL_PORT}")
    app.run(host=ADMIN_PANEL_HOST, port=ADMIN_PANEL_PORT, debug=False, ssl_context=ssl_context)


