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
from backup_manager import BackupManager
from database import get_db, OffloadLog
from config import SCHEDULER_TIMEZONE, SCHEDULER_HOUR, SCHEDULER_MINUTE
import logging
import threading
import requests

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
        self.backup_manager = BackupManager()
        
        # Add a lock to prevent overlapping runs
        self._job_lock = threading.Lock()
        self._job_running = False
        self._backup_lock = threading.Lock()
        self._backup_running = False
    
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
    
    def backup_job(self):
        """Job to create daily backup and send to Telegram/Slack"""
        # Check if a backup is already running
        if self._backup_running:
            logger.warning("Backup already running, skipping this execution")
            return
        
        # Acquire lock
        acquired = self._backup_lock.acquire(blocking=False)
        if not acquired:
            logger.warning("Could not acquire backup lock, another backup is running")
            return
        
        try:
            self._backup_running = True
            logger.info(f"Backup job started at {datetime.utcnow()}")
            print(f"Backup job started at {datetime.utcnow()}")
            
            # Create backup
            success, backup_path, summary = self.backup_manager.create_full_backup()
            
            if not success:
                error_msg = f"❌ Backup failed: {summary.get('error', 'Unknown error')}"
                logger.error(error_msg)
                
                # Send failure notification
                self.telegram_reporter.send_message(error_msg)
                return
            
            # Format success message
            timestamp = summary['timestamp'].strftime('%Y-%m-%d %H:%M:%S')
            size_mb = summary['size_mb']
            filename = summary['backup_file']
            
            message = f"""
🔄 <b>Automated Daily Backup</b>

✅ Backup created successfully

📅 <b>Date:</b> {timestamp} UTC
📦 <b>File:</b> {filename}
💾 <b>Size:</b> {size_mb:.2f} MB

📋 <b>Contents:</b>
• Application code
• Database (tickets.db)
• Configuration files
• Logs and archives

The backup file is being sent to you now...
"""
            
            # Send notification message
            logger.info("Sending backup notification to Telegram and Slack")
            self.telegram_reporter.send_message(message.strip())
            
            # Send backup file to Telegram
            logger.info("Uploading backup file to Telegram...")
            caption = f"📦 Z2W Backup - {timestamp} UTC ({size_mb:.2f} MB)"
            telegram_sent = self.telegram_reporter.send_file(backup_path, caption=caption)
            
            if telegram_sent:
                logger.info("Backup file sent to Telegram successfully")
            else:
                logger.warning("Failed to send backup file to Telegram")
            
            # Send backup notification to Slack (file upload requires SLACK_BOT_TOKEN)
            slack_message = f"🔄 *Automated Daily Backup*\n\n✅ Backup created successfully\n\n*Date:* {timestamp} UTC\n*File:* {filename}\n*Size:* {size_mb:.2f} MB\n\n*Contents:* Application code, Database, Configuration, Logs"
            
            # Try to send file to Slack if bot token is configured
            slack_file_sent = self.slack_reporter.send_file(backup_path, caption=slack_message)
            
            if slack_file_sent:
                logger.info("Backup file sent to Slack successfully")
            else:
                logger.info("Slack file upload not configured or failed, sending notification only")
                # Send text notification via webhook
                slack_payload = {
                    "text": slack_message,
                    "blocks": [
                        {
                            "type": "header",
                            "text": {
                                "type": "plain_text",
                                "text": "🔄 Automated Daily Backup"
                            }
                        },
                        {
                            "type": "section",
                            "fields": [
                                {"type": "mrkdwn", "text": f"*Status:*\n✅ Success"},
                                {"type": "mrkdwn", "text": f"*Date:*\n{timestamp} UTC"},
                                {"type": "mrkdwn", "text": f"*File:*\n{filename}"},
                                {"type": "mrkdwn", "text": f"*Size:*\n{size_mb:.2f} MB"}
                            ]
                        }
                    ]
                }
                try:
                    if self.slack_reporter.webhook_url:
                        requests.post(self.slack_reporter.webhook_url, json=slack_payload, timeout=10)
                except:
                    pass
            
            # Get backup info
            backup_info = self.backup_manager.get_backup_info()
            logger.info(f"Total backups: {backup_info['count']}, Total size: {backup_info['total_size_mb']:.2f} MB")
            
            logger.info(f"Backup job completed at {datetime.utcnow()}")
            print(f"Backup job completed at {datetime.utcnow()}")
            
        except Exception as e:
            error_msg = f"Error in backup job: {e}"
            logger.error(error_msg, exc_info=True)
            print(f"ERROR in backup job: {e}")
            
            # Send error notification
            try:
                self.telegram_reporter.send_message(f"❌ <b>Backup Failed</b>\n\nError: {str(e)}")
            except:
                pass
        finally:
            self._backup_running = False
            self._backup_lock.release()
    
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
            
            # Schedule backup job daily at 00:00 in configured timezone
            self.scheduler.add_job(
                self.backup_job,
                trigger=CronTrigger(hour=0, minute=0, timezone=SCHEDULER_TIMEZONE),
                id='daily_backup',
                name='Daily Backup',
                replace_existing=True
            )
            logger.info(f"Scheduled daily backup job for 00:00 {SCHEDULER_TIMEZONE}")
            
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
    
    def run_backup_now(self):
        """Manually trigger the backup job"""
        self.backup_job()



