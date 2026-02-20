"""
Configuration management for Zendesk to Wasabi B2 offloader
"""
import os
from pathlib import Path
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Base directory
BASE_DIR = Path(__file__).parent

# Database
DATABASE_PATH = BASE_DIR / "tickets.db"

# Zendesk Configuration
ZENDESK_SUBDOMAIN = os.getenv("ZENDESK_SUBDOMAIN", "")
ZENDESK_EMAIL = os.getenv("ZENDESK_EMAIL", "")
ZENDESK_API_TOKEN = os.getenv("ZENDESK_API_TOKEN", "")

# Wasabi B2 Configuration
WASABI_ENDPOINT = os.getenv("WASABI_ENDPOINT", "")
WASABI_ACCESS_KEY = os.getenv("WASABI_ACCESS_KEY", "")
WASABI_SECRET_KEY = os.getenv("WASABI_SECRET_KEY", "")
WASABI_BUCKET_NAME = os.getenv("WASABI_BUCKET_NAME", "")

# Email Configuration
SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
REPORT_EMAIL = os.getenv("REPORT_EMAIL", "it@go4rex.com")

# Telegram Configuration
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# Slack Configuration
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")

# Scheduler Configuration
SCHEDULER_TIMEZONE = os.getenv("SCHEDULER_TIMEZONE", "UTC")
SCHEDULER_HOUR = int(os.getenv("SCHEDULER_HOUR", "0"))
SCHEDULER_MINUTE = int(os.getenv("SCHEDULER_MINUTE", "0"))
RECHECK_HOUR = int(os.getenv("RECHECK_HOUR", "2"))
CONTINUOUS_OFFLOAD_INTERVAL = int(os.getenv("CONTINUOUS_OFFLOAD_INTERVAL", "5"))  # minutes
STORAGE_REPORT_INTERVAL = int(os.getenv("STORAGE_REPORT_INTERVAL", "60"))  # minutes between storage report refreshes

ATTACH_OFFLOAD_ENABLED = os.getenv("ATTACH_OFFLOAD_ENABLED", "true").lower() in ("1", "true", "yes", "on")
ATTACH_OFFLOAD_INTERVAL_MINUTES = int(os.getenv("ATTACH_OFFLOAD_INTERVAL_MINUTES", "60"))
ATTACH_OFFLOAD_ENDPOINT = os.getenv("ATTACH_OFFLOAD_ENDPOINT", "s3.wasabisys.com")
ATTACH_OFFLOAD_BUCKET = os.getenv("ATTACH_OFFLOAD_BUCKET", "supportmailboxattachments")
ATTACH_OFFLOAD_DAILY_LIMIT = int(os.getenv("ATTACH_OFFLOAD_DAILY_LIMIT", "0"))  # 0 = no limit

# Closed Ticket Backup Configuration (separate scheduler/job)
TICKET_BACKUP_ENABLED = os.getenv("TICKET_BACKUP_ENABLED", "true").lower() in ("1", "true", "yes", "on")
TICKET_BACKUP_INTERVAL_MINUTES = int(os.getenv("TICKET_BACKUP_INTERVAL_MINUTES", "1440"))
TICKET_BACKUP_ENDPOINT = os.getenv("TICKET_BACKUP_ENDPOINT", "s3.eu-central-1.wasabisys.com")
TICKET_BACKUP_BUCKET = os.getenv("TICKET_BACKUP_BUCKET", "supportmailboxtickets")
TICKET_BACKUP_DAILY_LIMIT = int(os.getenv("TICKET_BACKUP_DAILY_LIMIT", "0"))  # 0 = no limit

# Tickets Backup Scheduler
TICKET_BACKUP_TIME = os.getenv("TICKET_BACKUP_TIME", "01:00")
TICKET_BACKUP_MAX_PER_RUN = int(os.getenv("TICKET_BACKUP_MAX_PER_RUN", "0"))  # 0 = unlimited

# Attachment Offload Scheduler
OFFLOAD_TIME = os.getenv("OFFLOAD_TIME", "00:00")
MAX_ATTACHMENTS_PER_RUN = int(os.getenv("MAX_ATTACHMENTS_PER_RUN", "0"))  # 0 = unlimited

# Admin Panel
ADMIN_PANEL_PORT = int(os.getenv("ADMIN_PANEL_PORT", "5000"))
ADMIN_PANEL_HOST = os.getenv("ADMIN_PANEL_HOST", "0.0.0.0")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "4Ur@k?WU7eq&Frm8AK+%bxcruq82N4^T")

# SSL/HTTPS Configuration
SSL_CERT_PATH = os.getenv("SSL_CERT_PATH", "")
SSL_KEY_PATH = os.getenv("SSL_KEY_PATH", "")

# Office 365 OAuth Configuration
OAUTH_CLIENT_ID = os.getenv("OAUTH_CLIENT_ID", "")
OAUTH_CLIENT_SECRET = os.getenv("OAUTH_CLIENT_SECRET", "")
OAUTH_AUTHORITY = os.getenv("OAUTH_AUTHORITY", "https://login.microsoftonline.com/common")
OAUTH_REDIRECT_PATH = os.getenv("OAUTH_REDIRECT_PATH", "/getAToken")
OAUTH_SCOPES = ["User.Read"]

# Allowed email domains for OAuth
ALLOWED_DOMAINS = [
    "heimman.com",
    "rhcsolutions.com",
    "synergybeam.com",
    "iccswitch.com",
    "go4rex.com"
]

def reload_config():
    """Reload environment variables from .env file"""
    load_dotenv(override=True)
    global ZENDESK_SUBDOMAIN, ZENDESK_EMAIL, ZENDESK_API_TOKEN
    global WASABI_ENDPOINT, WASABI_ACCESS_KEY, WASABI_SECRET_KEY, WASABI_BUCKET_NAME
    global SMTP_SERVER, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, REPORT_EMAIL
    global TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, SLACK_WEBHOOK_URL
    global ADMIN_USERNAME, ADMIN_PASSWORD
    global SSL_CERT_PATH, SSL_KEY_PATH
    global OAUTH_CLIENT_ID, OAUTH_CLIENT_SECRET, OAUTH_AUTHORITY, OAUTH_REDIRECT_PATH
    global SCHEDULER_TIMEZONE, SCHEDULER_HOUR, SCHEDULER_MINUTE
    global RECHECK_HOUR, CONTINUOUS_OFFLOAD_INTERVAL, STORAGE_REPORT_INTERVAL
    global TICKET_BACKUP_ENABLED, TICKET_BACKUP_INTERVAL_MINUTES
    global TICKET_BACKUP_ENDPOINT, TICKET_BACKUP_BUCKET, TICKET_BACKUP_DAILY_LIMIT
    
    ZENDESK_SUBDOMAIN = os.getenv("ZENDESK_SUBDOMAIN", "")
    ZENDESK_EMAIL = os.getenv("ZENDESK_EMAIL", "")
    ZENDESK_API_TOKEN = os.getenv("ZENDESK_API_TOKEN", "")
    
    WASABI_ENDPOINT = os.getenv("WASABI_ENDPOINT", "")
    WASABI_ACCESS_KEY = os.getenv("WASABI_ACCESS_KEY", "")
    WASABI_SECRET_KEY = os.getenv("WASABI_SECRET_KEY", "")
    WASABI_BUCKET_NAME = os.getenv("WASABI_BUCKET_NAME", "")
    
    SMTP_SERVER = os.getenv("SMTP_SERVER", "smtp.gmail.com")
    SMTP_PORT = int(os.getenv("SMTP_PORT", "587"))
    SMTP_USERNAME = os.getenv("SMTP_USERNAME", "")
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
    REPORT_EMAIL = os.getenv("REPORT_EMAIL", "it@go4rex.com")
    
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
    SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")

    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "4Ur@k?WU7eq&Frm8AK+%bxcruq82N4^T")
    
    SSL_CERT_PATH = os.getenv("SSL_CERT_PATH", "")
    SSL_KEY_PATH = os.getenv("SSL_KEY_PATH", "")
    
    OAUTH_CLIENT_ID = os.getenv("OAUTH_CLIENT_ID", "")
    OAUTH_CLIENT_SECRET = os.getenv("OAUTH_CLIENT_SECRET", "")
    OAUTH_AUTHORITY = os.getenv("OAUTH_AUTHORITY", "https://login.microsoftonline.com/common")
    OAUTH_REDIRECT_PATH = os.getenv("OAUTH_REDIRECT_PATH", "/getAToken")
    
    SCHEDULER_TIMEZONE = os.getenv("SCHEDULER_TIMEZONE", "UTC")
    SCHEDULER_HOUR = int(os.getenv("SCHEDULER_HOUR", "0"))
    SCHEDULER_MINUTE = int(os.getenv("SCHEDULER_MINUTE", "0"))
    RECHECK_HOUR = int(os.getenv("RECHECK_HOUR", "2"))
    CONTINUOUS_OFFLOAD_INTERVAL = int(os.getenv("CONTINUOUS_OFFLOAD_INTERVAL", "5"))
    STORAGE_REPORT_INTERVAL = int(os.getenv("STORAGE_REPORT_INTERVAL", "60"))

    TICKET_BACKUP_ENABLED = os.getenv("TICKET_BACKUP_ENABLED", "true").lower() in ("1", "true", "yes", "on")
    TICKET_BACKUP_INTERVAL_MINUTES = int(os.getenv("TICKET_BACKUP_INTERVAL_MINUTES", "1440"))
    TICKET_BACKUP_ENDPOINT = os.getenv("TICKET_BACKUP_ENDPOINT", "s3.eu-central-1.wasabisys.com")
    TICKET_BACKUP_BUCKET = os.getenv("TICKET_BACKUP_BUCKET", "supportmailboxtickets")
    TICKET_BACKUP_DAILY_LIMIT = int(os.getenv("TICKET_BACKUP_DAILY_LIMIT", "0"))


