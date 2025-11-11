"""
Main entry point for Zendesk to Wasabi B2 Offloader
"""
from database import init_db
from admin_panel import app, init_scheduler
from config import ADMIN_PANEL_PORT, ADMIN_PANEL_HOST

if __name__ == '__main__':
    # Initialize database
    print("Initializing database...")
    init_db()
    
    # Initialize and start scheduler
    print("Starting scheduler...")
    scheduler = init_scheduler()
    scheduler.start()
    
    # Run Flask admin panel
    print(f"Starting admin panel on http://{ADMIN_PANEL_HOST}:{ADMIN_PANEL_PORT}")
    app.run(host=ADMIN_PANEL_HOST, port=ADMIN_PANEL_PORT, debug=False)


