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

# Scheduler Configuration
SCHEDULER_TIMEZONE = "UTC"
SCHEDULER_HOUR = 0
SCHEDULER_MINUTE = 0

# Admin Panel
ADMIN_PANEL_PORT = int(os.getenv("ADMIN_PANEL_PORT", "5000"))
ADMIN_PANEL_HOST = os.getenv("ADMIN_PANEL_HOST", "0.0.0.0")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-key-change-in-production")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "4Ur@k?WU7eq&Frm8AK+%bxcruq82N4^T")

def reload_config():
    """Reload environment variables from .env file"""
    load_dotenv(override=True)
    global ZENDESK_SUBDOMAIN, ZENDESK_EMAIL, ZENDESK_API_TOKEN
    global WASABI_ENDPOINT, WASABI_ACCESS_KEY, WASABI_SECRET_KEY, WASABI_BUCKET_NAME
    global SMTP_SERVER, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, REPORT_EMAIL
    global ADMIN_USERNAME, ADMIN_PASSWORD
    
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

    ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "4Ur@k?WU7eq&Frm8AK+%bxcruq82N4^T")


