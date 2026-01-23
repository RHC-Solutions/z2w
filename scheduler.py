"""
Scheduler for daily automatic offload
"""
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime
from offloader import AttachmentOffloader
from email_reporter import EmailReporter
from telegram_reporter import TelegramReporter
from slack_reporter import SlackReporter
from database import get_db, OffloadLog
from config import SCHEDULER_TIMEZONE, SCHEDULER_HOUR, SCHEDULER_MINUTE
import logging
import threading

# Get logger
logger = logging.getLogger('zendesk_offloader')

class OffloadScheduler:
    """Manage scheduled offload jobs"""
    
    def __init__(self):
        # Re-import config to get latest values
        from config import SCHEDULER_TIMEZONE
        
        # Initialize scheduler with timezone handling for Linux/Debian compatibility
        try:
            self.scheduler = BackgroundScheduler(timezone=SCHEDULER_TIMEZONE)
            logger.info(f"Scheduler initialized with timezone: {SCHEDULER_TIMEZONE}")
        except Exception as e:
            logger.warning(f"Failed to initialize scheduler with timezone {SCHEDULER_TIMEZONE}: {e}")
            logger.info("Falling back to UTC timezone")
            self.scheduler = BackgroundScheduler(timezone='UTC')
        
        self.offloader = AttachmentOffloader()
        self.email_reporter = EmailReporter()
        self.telegram_reporter = TelegramReporter()
        self.slack_reporter = SlackReporter()
        
        # Add a lock to prevent overlapping runs
        self._job_lock = threading.Lock()
        self._job_running = False
    
    def scheduled_job(self):
        """Job to run daily at 00:00 GMT"""
        # Check if a job is already running
        if self._job_running:
            logger.warning("Job already running, skipping this execution")
            print("Job already running, skipping this execution")
            return
        
        # Acquire lock
        acquired = self._job_lock.acquire(blocking=False)
        if not acquired:
            logger.warning("Could not acquire job lock, another job is running")
            print("Could not acquire job lock, another job is running")
            return
        
        try:
            self._job_running = True
            logger.info(f"Scheduled job started at {datetime.utcnow()}")
            print(f"Scheduled job started at {datetime.utcnow()}")
            
            # Run offload
            summary = self.offloader.run_offload()
            
            # Send reports to all configured channels
            email_sent = self.email_reporter.send_report(summary)
            telegram_sent = self.telegram_reporter.send_report(summary)
            slack_sent = self.slack_reporter.send_report(summary)
            
            # Log report sending status
            logger.info(f"Reports sent - Email: {email_sent}, Telegram: {telegram_sent}, Slack: {slack_sent}")
            
            # Update log entry
            db = get_db()
            try:
                if summary.get("log_id"):
                    log_entry = db.query(OffloadLog).filter_by(id=summary["log_id"]).first()
                    if log_entry:
                        # Mark as sent if at least one channel succeeded
                        log_entry.report_sent = email_sent or telegram_sent or slack_sent
                        db.commit()
            finally:
                db.close()
            
            logger.info(f"Scheduled job completed at {datetime.utcnow()}")
            print(f"Scheduled job completed at {datetime.utcnow()}")
            
        except Exception as e:
            logger.error(f"Error in scheduled job: {e}", exc_info=True)
            print(f"ERROR in scheduled job: {e}")
        finally:
            self._job_running = False
            self._job_lock.release()
    
    def archive_logs_job(self):
        """Job to archive old logs daily"""
        from logger_config import archive_old_logs
        logger.info("Running daily log archiving job...")
        archive_old_logs(days_to_keep=7)
    
    def start(self):
        """Start the scheduler"""
        # Re-import config to get latest values
        from config import SCHEDULER_TIMEZONE, SCHEDULER_HOUR, SCHEDULER_MINUTE
        
        try:
            # Schedule daily job at configured time
            self.scheduler.add_job(
                self.scheduled_job,
                trigger=CronTrigger(hour=SCHEDULER_HOUR, minute=SCHEDULER_MINUTE, timezone=SCHEDULER_TIMEZONE),
                id='daily_offload',
                name='Daily Zendesk Offload',
                replace_existing=True
            )
            logger.info(f"Scheduled daily offload job for {SCHEDULER_HOUR:02d}:{SCHEDULER_MINUTE:02d} {SCHEDULER_TIMEZONE}")
            
            # Schedule log archiving job daily at 01:00 in configured timezone (after offload job)
            self.scheduler.add_job(
                self.archive_logs_job,
                trigger=CronTrigger(hour=1, minute=0, timezone=SCHEDULER_TIMEZONE),
                id='archive_logs',
                name='Daily Log Archiving',
                replace_existing=True
            )
            logger.info(f"Scheduled log archiving job for 01:00 {SCHEDULER_TIMEZONE}")
            
            self.scheduler.start()
            
            # Log all scheduled jobs
            jobs = self.scheduler.get_jobs()
            if jobs:
                for job in jobs:
                    logger.info(f"Job '{job.name}' (ID: {job.id}) - Next run: {job.next_run_time}")
                    print(f"Job '{job.name}' - Next run: {job.next_run_time}")
            else:
                logger.warning("No jobs scheduled!")
                print("WARNING: No jobs scheduled!")
            
            logger.info("Scheduler started successfully")
            print("Scheduler started successfully")
            
        except Exception as e:
            logger.error(f"Failed to start scheduler: {e}", exc_info=True)
            print(f"ERROR: Failed to start scheduler: {e}")
            raise
    
    def stop(self):
        """Stop the scheduler"""
        self.scheduler.shutdown()
    
    def run_now(self):
        """Manually trigger the offload job"""
        self.scheduled_job()


