"""
Scheduler for daily automatic offload
"""
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime
from offloader import AttachmentOffloader
from email_reporter import EmailReporter
from database import get_db, OffloadLog
from config import SCHEDULER_TIMEZONE, SCHEDULER_HOUR, SCHEDULER_MINUTE

class OffloadScheduler:
    """Manage scheduled offload jobs"""
    
    def __init__(self):
        self.scheduler = BackgroundScheduler(timezone=SCHEDULER_TIMEZONE)
        self.offloader = AttachmentOffloader()
        self.email_reporter = EmailReporter()
    
    def scheduled_job(self):
        """Job to run daily at 00:00 GMT"""
        print(f"Scheduled job started at {datetime.utcnow()}")
        
        # Run offload
        summary = self.offloader.run_offload()
        
        # Send email report
        email_sent = self.email_reporter.send_report(summary)
        
        # Update log entry
        db = get_db()
        try:
            if summary.get("log_id"):
                log_entry = db.query(OffloadLog).filter_by(id=summary["log_id"]).first()
                if log_entry:
                    log_entry.report_sent = email_sent
                    db.commit()
        finally:
            db.close()
        
        print(f"Scheduled job completed at {datetime.utcnow()}")
    
    def start(self):
        """Start the scheduler"""
        # Schedule daily job at 00:00 GMT
        self.scheduler.add_job(
            self.scheduled_job,
            trigger=CronTrigger(hour=SCHEDULER_HOUR, minute=SCHEDULER_MINUTE, timezone=SCHEDULER_TIMEZONE),
            id='daily_offload',
            name='Daily Zendesk Offload',
            replace_existing=True
        )
        
        self.scheduler.start()
        print(f"Scheduler started. Next run: {self.scheduler.get_jobs()[0].next_run_time}")
    
    def stop(self):
        """Stop the scheduler"""
        self.scheduler.shutdown()
    
    def run_now(self):
        """Manually trigger the offload job"""
        self.scheduled_job()


