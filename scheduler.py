"""
Scheduler for daily automatic offload
"""
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime, timedelta
from offloader import AttachmentOffloader
from email_reporter import EmailReporter
from telegram_reporter import TelegramReporter
from slack_reporter import SlackReporter
from backup_manager import BackupManager
from ticket_backup_manager import TicketBackupManager
from database import get_db, OffloadLog, ZendeskStorageSnapshot, Setting, TicketBackupRun
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
        self.ticket_backup_manager = TicketBackupManager()
        
        # Add a lock to prevent overlapping runs
        self._job_lock = threading.Lock()
        self._job_running = False
        self._backup_lock = threading.Lock()
        self._backup_running = False
        self._continuous_lock = threading.Lock()
        self._continuous_running = False
        self._ticket_backup_lock = threading.Lock()
        self._ticket_backup_running = False
        self._ticket_backup_started_at = None
        self._ticket_backup_last_summary = None
        self._storage_lock = threading.Lock()
        self._storage_running = False

    # ── helpers shared by full / delta snapshot paths ──────────────────
    def _scan_ticket_storage(self, tid, subj, zd_status, db, now):
        """Fetch comments for *one* ticket from Zendesk, count attachments and
        inline images, and upsert the result into zendesk_storage_snapshot.
        Returns (ticket_size_bytes, True) on success, (0, False) on error/skip."""
        import re
        url = f"{self.offloader.zendesk.base_url}/tickets/{tid}/comments.json"
        resp = self.offloader.zendesk.session.get(url, timeout=15)
        if resp.status_code == 404:
            return 0, True          # ticket deleted — skip silently
        if resp.status_code == 429:
            import time as _time
            retry_after = int(resp.headers.get('Retry-After', 10))
            logger.warning(f"[StorageSnapshot] Rate limited, sleeping {retry_after}s")
            _time.sleep(retry_after)
            return 0, False
        if not resp.ok:
            return 0, False

        comments = resp.json().get("comments", [])
        attach_count = 0
        inline_count = 0
        ticket_size = 0
        for c in comments:
            for a in c.get("attachments", []):
                fname = a.get("file_name", "")
                if fname.lower().endswith("redacted.txt"):
                    continue
                attach_count += 1
                ticket_size += a.get("size", 0)
            html = c.get("html_body", "") or ""
            for _ in re.finditer(r'src="https://[^"]*zendesk[^"]*attachments[^"]*"', html, re.IGNORECASE):
                inline_count += 1

        row = db.query(ZendeskStorageSnapshot).filter_by(ticket_id=tid).first()
        if row:
            row.subject      = subj or ""
            row.zd_status    = zd_status
            row.attach_count = attach_count
            row.inline_count = inline_count
            row.total_size   = ticket_size
            row.last_seen_at = now
            row.updated_at   = now
        else:
            db.add(ZendeskStorageSnapshot(
                ticket_id    = tid,
                subject      = subj or "",
                zd_status    = zd_status,
                attach_count = attach_count,
                inline_count = inline_count,
                total_size   = ticket_size,
                last_seen_at = now,
                updated_at   = now,
            ))
        return ticket_size, True

    def storage_snapshot_job(self):
        """Interval job: scan Zendesk tickets and record their current storage
        usage in the zendesk_storage_snapshot table.

        Uses **delta mode** by default — only re-scans tickets updated since the
        last successful run (tracked via the ``STORAGE_SNAPSHOT_LAST_TS`` setting).
        Falls back to a full scan on the very first run or when the setting is
        missing.  A full scan can also be forced by setting the setting value to
        ``"0"``.

        Skips if already running."""
        if self._storage_running:
            logger.debug("[StorageSnapshot] Previous run still active — skipping")
            return
        acquired = self._storage_lock.acquire(blocking=False)
        if not acquired:
            return
        try:
            self._storage_running = True
            logger.info("[StorageSnapshot] Starting storage snapshot refresh…")
            start = datetime.utcnow()

            import math
            import time as _time
            from database import ZendeskTicketCache, Setting

            SETTING_KEY = 'STORAGE_SNAPSHOT_LAST_TS'
            batch_size = 50

            # ── Determine mode: delta vs full ─────────────────────────────
            db = get_db()
            try:
                setting_row = db.query(Setting).filter_by(key=SETTING_KEY).first()
                last_ts = None
                if setting_row and setting_row.value and setting_row.value != '0':
                    try:
                        last_ts = float(setting_row.value)
                    except (ValueError, TypeError):
                        pass
            finally:
                db.close()

            import calendar
            # Use UTC-based unix timestamp to avoid local-clock/DST issues
            run_start_ts = calendar.timegm(start.utctimetuple())

            # Treat future-dated or invalid timestamps as missing (force full scan)
            now_utc_ts = run_start_ts
            if last_ts is not None and last_ts > now_utc_ts:
                logger.warning(
                    f"[StorageSnapshot] Stored timestamp {last_ts} is in the future "
                    f"(now={now_utc_ts}) — resetting to force full scan"
                )
                last_ts = None

            is_full_scan = last_ts is None

            if is_full_scan:
                # ── FULL SCAN (first run / forced) ────────────────────────
                logger.info("[StorageSnapshot] No previous timestamp — running FULL scan")
                # Persist timestamp NOW so a restart mid-scan won't trigger a second full scan
                db = get_db()
                try:
                    _guard = db.query(Setting).filter_by(key=SETTING_KEY).first()
                    if _guard is None:
                        db.add(Setting(key=SETTING_KEY, value=str(run_start_ts),
                                       description='Unix timestamp of last successful storage snapshot run'))
                    else:
                        _guard.value = str(run_start_ts)
                    db.commit()
                except Exception as _e:
                    logger.warning(f"[StorageSnapshot] Could not pre-save timestamp: {_e}")
                    db.rollback()
                finally:
                    db.close()
                db = get_db()
                try:
                    cache_tuples = (
                        db.query(
                            ZendeskTicketCache.ticket_id,
                            ZendeskTicketCache.subject,
                            ZendeskTicketCache.status,
                        ).all()
                    )
                finally:
                    db.close()

                total_tickets = len(cache_tuples)
                logger.info(f"[StorageSnapshot] Full scan: {total_tickets} cached tickets")
                now = datetime.utcnow()
                updated = 0
                errors = 0
                total_size = 0

                db = get_db()
                try:
                    for idx, (tid, subj, zd_status) in enumerate(cache_tuples):
                        try:
                            tsize, ok = self._scan_ticket_storage(tid, subj, zd_status, db, now)
                            if ok:
                                updated += 1
                                total_size += tsize
                            else:
                                errors += 1

                            if updated % batch_size == 0 and updated > 0:
                                for _retry in range(3):
                                    try:
                                        db.commit()
                                        break
                                    except Exception as ce:
                                        db.rollback()
                                        if 'locked' in str(ce).lower() and _retry < 2:
                                            _time.sleep(1 * (_retry + 1))
                                            continue
                                        raise
                                db.close()
                                db = get_db()
                                logger.info(f"[StorageSnapshot] progress {updated}/{total_tickets}…")

                        except Exception as e:
                            errors += 1
                            if 'locked' in str(e).lower():
                                logger.warning(f"[StorageSnapshot] ticket {tid}: DB locked, will retry on next run")
                                db.rollback()
                            else:
                                logger.warning(f"[StorageSnapshot] ticket {tid}: {e}")
                                try:
                                    db.rollback()
                                except Exception:
                                    pass

                    # Final commit
                    for _retry in range(3):
                        try:
                            db.commit()
                            break
                        except Exception as ce:
                            db.rollback()
                            if 'locked' in str(ce).lower() and _retry < 2:
                                _time.sleep(1 * (_retry + 1))
                                continue
                            logger.error(f"[StorageSnapshot] Final commit failed: {ce}")
                finally:
                    db.close()

            else:
                # ── DELTA SCAN ────────────────────────────────────────────
                logger.info(f"[StorageSnapshot] Delta scan — tickets updated since {datetime.utcfromtimestamp(last_ts).strftime('%Y-%m-%d %H:%M:%S')} UTC")

                # Use Zendesk incremental API to get only changed tickets
                changed_ids = set()
                inc_url = f"{self.offloader.zendesk.base_url}/incremental/tickets.json"
                params = {"start_time": int(last_ts)}
                while inc_url:
                    try:
                        resp = self.offloader.zendesk.session.get(inc_url, params=params, timeout=30)
                        if resp.status_code == 429:
                            retry_after = int(resp.headers.get('Retry-After', 30))
                            logger.warning(f"[StorageSnapshot] Rate limited during incremental fetch, waiting {retry_after}s")
                            _time.sleep(retry_after)
                            continue
                        if resp.status_code == 422:
                            logger.info("[StorageSnapshot] start_time too recent, nothing to delta-scan")
                            break
                        resp.raise_for_status()
                        data = resp.json()
                        for t in data.get("tickets", []):
                            if t.get("status") != "deleted":
                                changed_ids.add(t["id"])
                        if data.get("end_of_stream", True):
                            break
                        next_page = data.get("next_page")
                        if not next_page:
                            break
                        inc_url = next_page
                        params = None
                    except Exception as e:
                        logger.error(f"[StorageSnapshot] Incremental fetch error: {e}")
                        break

                total_tickets = len(changed_ids)
                logger.info(f"[StorageSnapshot] Delta: {total_tickets} ticket(s) changed since last run")

                if total_tickets == 0:
                    # Nothing changed — just update the timestamp and return
                    db = get_db()
                    try:
                        row = db.query(Setting).filter_by(key=SETTING_KEY).first()
                        if row:
                            row.value = str(run_start_ts)
                        db.commit()
                    finally:
                        db.close()
                    elapsed = (datetime.utcnow() - start).total_seconds()
                    logger.info(f"[StorageSnapshot] Done (no changes) — {elapsed:.0f}s")
                    self._storage_running = False
                    self._storage_lock.release()
                    return

                # Load metadata for changed tickets from cache
                db = get_db()
                try:
                    cache_map = {}
                    from database import ZendeskTicketCache as ZTC
                    rows = db.query(ZTC.ticket_id, ZTC.subject, ZTC.status).filter(
                        ZTC.ticket_id.in_(changed_ids)
                    ).all()
                    for tid, subj, status in rows:
                        cache_map[tid] = (subj, status)
                finally:
                    db.close()

                now = datetime.utcnow()
                updated = 0
                errors = 0
                total_size = 0

                db = get_db()
                try:
                    for tid in changed_ids:
                        subj, zd_status = cache_map.get(tid, ("", None))
                        try:
                            tsize, ok = self._scan_ticket_storage(tid, subj, zd_status, db, now)
                            if ok:
                                updated += 1
                                total_size += tsize
                            else:
                                errors += 1

                            if updated % batch_size == 0 and updated > 0:
                                for _retry in range(3):
                                    try:
                                        db.commit()
                                        break
                                    except Exception as ce:
                                        db.rollback()
                                        if 'locked' in str(ce).lower() and _retry < 2:
                                            _time.sleep(1 * (_retry + 1))
                                            continue
                                        raise
                                db.close()
                                db = get_db()
                                logger.info(f"[StorageSnapshot] delta progress {updated}/{total_tickets}…")

                        except Exception as e:
                            errors += 1
                            if 'locked' in str(e).lower():
                                logger.warning(f"[StorageSnapshot] ticket {tid}: DB locked, will retry on next run")
                                db.rollback()
                            else:
                                logger.warning(f"[StorageSnapshot] ticket {tid}: {e}")
                                try:
                                    db.rollback()
                                except Exception:
                                    pass

                    # Final commit
                    for _retry in range(3):
                        try:
                            db.commit()
                            break
                        except Exception as ce:
                            db.rollback()
                            if 'locked' in str(ce).lower() and _retry < 2:
                                _time.sleep(1 * (_retry + 1))
                                continue
                            logger.error(f"[StorageSnapshot] Final commit failed: {ce}")
                finally:
                    db.close()

            # ── Persist the timestamp so the next run is a delta ──────────
            db = get_db()
            try:
                row = db.query(Setting).filter_by(key=SETTING_KEY).first()
                if row:
                    row.value = str(run_start_ts)
                else:
                    db.add(Setting(key=SETTING_KEY, value=str(run_start_ts),
                                   description='Unix timestamp of last successful storage snapshot run'))
                db.commit()
            finally:
                db.close()

            elapsed = (datetime.utcnow() - start).total_seconds()
            mode = "FULL" if is_full_scan else "DELTA"
            logger.info(
                f"[StorageSnapshot] Done ({mode}) — {updated} tickets scanned, "
                f"{total_size/1024/1024:.1f} MB, {errors} errors, {elapsed:.0f}s"
            )

        except Exception as e:
            logger.error(f"[StorageSnapshot] Unhandled exception: {e}", exc_info=True)
        finally:
            self._storage_running = False
            self._storage_lock.release()
    
    def continuous_offload_job(self):
        """Interval job: offload attachments from tickets updated in the last window.
        Runs every CONTINUOUS_OFFLOAD_INTERVAL minutes. Skips if already running."""
        if self._continuous_running:
            logger.debug("[Continuous] Previous run still active — skipping")
            return
        acquired = self._continuous_lock.acquire(blocking=False)
        if not acquired:
            return
        try:
            self._continuous_running = True
            from config import CONTINUOUS_OFFLOAD_INTERVAL
            # Look back 2× the interval to cover any clock drift or slow Zendesk indexing
            lookback = max(CONTINUOUS_OFFLOAD_INTERVAL * 2, 10)
            stats = self.offloader.process_recent_tickets(since_minutes=lookback)
            newly = stats.get("newly_processed", 0)
            uploaded = stats.get("attachments_uploaded", 0)
            size_mb = stats.get("total_size_bytes", 0) / 1024 / 1024
            errors = stats.get("errors", [])
            if newly > 0 or errors:
                logger.info(
                    f"[Continuous] tickets={stats['checked']} skipped={stats['already_done']} "
                    f"new={newly} files={uploaded} size={size_mb:.1f}MB errors={len(errors)}"
                )
        except Exception as e:
            logger.error(f"[Continuous offload] Unhandled exception: {e}", exc_info=True)
        finally:
            self._continuous_running = False
            self._continuous_lock.release()

    def scheduled_job(self):
        """Job to run every 5 minutes — full offload including inline images"""
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
            start_time = datetime.utcnow()
            logger.info(f"Scheduled offload job started at {start_time}")

            # Run offload
            summary = self.offloader.run_offload()

            logger.info(f"Scheduled offload job completed at {datetime.utcnow()}")

        except Exception as e:
            logger.error(f"Error in scheduled job: {e}", exc_info=True)
            try:
                self.telegram_reporter.send_message(
                    f"❌ <b>Offload job failed</b>\n\n<code>{str(e)[:500]}</code>"
                )
            except Exception:
                pass
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
            server_name = summary.get('server_name', 'unknown')
            server_ip = summary.get('server_ip', 'unknown')
            server_user = summary.get('server_user', 'unknown')
            
            message = f"""
🔄 <b>Automated Daily Backup</b>

✅ Backup created successfully

🖥️ <b>Server Info:</b>
• Name: {server_name}
• IP: {server_ip}
• User: {server_user}

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
            caption = f"📦 Z2W Backup - {server_name} ({server_ip}) - {timestamp} UTC ({size_mb:.2f} MB)"
            telegram_sent = self.telegram_reporter.send_file(backup_path, caption=caption)
            
            if telegram_sent:
                logger.info("Backup file sent to Telegram successfully")
            else:
                logger.warning("Failed to send backup file to Telegram")
            
            # Send backup notification to Slack (file upload requires SLACK_BOT_TOKEN)
            slack_message = f"🔄 *Automated Daily Backup*\n\n✅ Backup created successfully\n\n*Server:* {server_name} ({server_ip})\n*User:* {server_user}\n*Date:* {timestamp} UTC\n*File:* {filename}\n*Size:* {size_mb:.2f} MB\n\n*Contents:* Application code, Database, Configuration, Logs"
            
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
                                {"type": "mrkdwn", "text": f"*Server:*\n{server_name} ({server_ip})"},
                                {"type": "mrkdwn", "text": f"*User:*\n{server_user}"},
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
    
    def daily_stats_job(self):
        """Run at 00:00 — aggregate the previous day's activity from OffloadLog +
        TicketBackupRun and send one rich daily summary to Telegram and Slack."""
        logger.info("[DailyStats] Building daily summary report…")
        try:
            from datetime import timedelta
            import re as _re

            now = datetime.utcnow()
            # "yesterday" window: from 00:00 to 23:59:59 of the day that just ended
            today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            yesterday_start = today_start - timedelta(days=1)
            yesterday_end   = today_start

            db = get_db()
            try:
                offload_logs = db.query(OffloadLog).filter(
                    OffloadLog.run_date >= yesterday_start,
                    OffloadLog.run_date <  yesterday_end,
                ).all()

                backup_runs = db.query(TicketBackupRun).filter(
                    TicketBackupRun.run_date >= yesterday_start,
                    TicketBackupRun.run_date <  yesterday_end,
                ).all() if hasattr(TicketBackupRun, 'run_date') else []
            finally:
                db.close()

            date_label = yesterday_start.strftime('%d %b %Y')

            # ── Offload totals ────────────────────────────────────────────
            total_runs       = len(offload_logs)
            total_tickets    = sum(r.tickets_processed or 0 for r in offload_logs)
            total_attachments= sum(r.attachments_uploaded or 0 for r in offload_logs)
            total_inlines    = sum(r.inlines_uploaded or 0 for r in offload_logs)
            total_errors     = sum(r.errors_count or 0 for r in offload_logs)

            # ── Backup totals ─────────────────────────────────────────────
            bak_runs         = len(backup_runs)
            bak_scanned      = sum(r.tickets_scanned or 0 for r in backup_runs)
            bak_backed_up    = sum(r.tickets_backed_up or 0 for r in backup_runs)
            bak_files        = sum(r.files_uploaded or 0 for r in backup_runs)
            bak_bytes        = sum(r.bytes_uploaded or 0 for r in backup_runs)
            bak_errors       = sum(r.errors_count or 0 for r in backup_runs)
            bak_mb           = bak_bytes / 1048576

            overall_ok = (total_errors == 0 and bak_errors == 0)
            emoji = '✅' if overall_ok else '⚠️'

            # ── Telegram (HTML) ───────────────────────────────────────────
            tg_msg = (
                f"{emoji} <b>Z2W Daily Report — {date_label}</b>\n"
                f"\n"
                f"📤 <b>Attachment Offload</b>\n"
                f"  • Runs: <b>{total_runs}</b>\n"
                f"  • Tickets processed: <b>{total_tickets:,}</b>\n"
                f"  • Attachments uploaded: <b>{total_attachments:,}</b>\n"
            )
            if total_inlines:
                tg_msg += f"  • Inline images uploaded: <b>{total_inlines:,}</b>\n"
            tg_msg += f"  • Errors: <b>{total_errors}</b>\n"

            tg_msg += (
                f"\n"
                f"💾 <b>Closed-Ticket Backup</b>\n"
                f"  • Runs: <b>{bak_runs}</b>\n"
                f"  • Tickets scanned: <b>{bak_scanned:,}</b>\n"
                f"  • Tickets backed up: <b>{bak_backed_up:,}</b>\n"
                f"  • Files uploaded: <b>{bak_files:,}</b>\n"
                f"  • Data uploaded: <b>{bak_mb:.1f} MB</b>\n"
                f"  • Errors: <b>{bak_errors}</b>\n"
            )

            if not overall_ok:
                err_detail = []
                for r in offload_logs:
                    if r.errors_count:
                        err_detail.append(f"  Offload run {r.run_date.strftime('%H:%M')}: {r.errors_count} error(s)")
                for r in backup_runs:
                    if r.errors_count:
                        err_detail.append(f"  Backup run {r.run_date.strftime('%H:%M')}: {r.errors_count} error(s)")
                if err_detail:
                    tg_msg += "\n❌ <b>Error Detail:</b>\n" + "\n".join(err_detail[:10]) + "\n"

            try:
                self.telegram_reporter.send_message(tg_msg.strip())
                logger.info("[DailyStats] Telegram report sent")
            except Exception as te:
                logger.error(f"[DailyStats] Telegram send failed: {te}")

            # ── Slack (blocks) ────────────────────────────────────────────
            def _strip_html(s):
                return _re.sub(r'<[^>]+>', '', s)

            status_text = "✅ All clear" if overall_ok else "⚠️ Errors detected"
            slack_payload = {
                "blocks": [
                    {
                        "type": "header",
                        "text": {"type": "plain_text", "text": f"{emoji} Z2W Daily Report — {date_label}"}
                    },
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": f"*Status:* {status_text}"}
                    },
                    {"type": "divider"},
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": "*📤 Attachment Offload*"},
                        "fields": [
                            {"type": "mrkdwn", "text": f"*Runs*\n{total_runs}"},
                            {"type": "mrkdwn", "text": f"*Tickets processed*\n{total_tickets:,}"},
                            {"type": "mrkdwn", "text": f"*Attachments uploaded*\n{total_attachments:,}"},
                            {"type": "mrkdwn", "text": f"*Errors*\n{total_errors}"},
                        ]
                    },
                    {"type": "divider"},
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": "*💾 Closed-Ticket Backup*"},
                        "fields": [
                            {"type": "mrkdwn", "text": f"*Runs*\n{bak_runs}"},
                            {"type": "mrkdwn", "text": f"*Tickets backed up*\n{bak_backed_up:,} / {bak_scanned:,}"},
                            {"type": "mrkdwn", "text": f"*Files uploaded*\n{bak_files:,}"},
                            {"type": "mrkdwn", "text": f"*Data uploaded*\n{bak_mb:.1f} MB"},
                            {"type": "mrkdwn", "text": f"*Errors*\n{bak_errors}"},
                        ]
                    },
                    {
                        "type": "context",
                        "elements": [{"type": "mrkdwn", "text": f"Z2W · {now.strftime('%Y-%m-%d %H:%M')} UTC"}]
                    },
                ]
            }
            try:
                if self.slack_reporter.webhook_url:
                    import requests as _req
                    _req.post(self.slack_reporter.webhook_url, json=slack_payload, timeout=10)
                    logger.info("[DailyStats] Slack report sent")
            except Exception as se:
                logger.error(f"[DailyStats] Slack send failed: {se}")

        except Exception as e:
            logger.error(f"[DailyStats] Unhandled exception: {e}", exc_info=True)

    def start(self):
        """Start the scheduler"""
        # Re-import config to get latest values
        from config import SCHEDULER_TIMEZONE, SCHEDULER_HOUR, SCHEDULER_MINUTE
        
        try:
            # Schedule offload job — runs every 5 minutes, full offload with inline images
            self.scheduler.add_job(
                self.scheduled_job,
                trigger=IntervalTrigger(minutes=5),
                id='daily_offload',
                name='Offload Every 5 Minutes',
                replace_existing=True,
                next_run_time=datetime.now()  # start immediately on boot
            )
            logger.info("Scheduled offload job every 5 minutes")
            
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

            # Schedule daily statistics report at 00:01 (after daily_backup at 00:00)
            self.scheduler.add_job(
                self.daily_stats_job,
                trigger=CronTrigger(hour=0, minute=1, timezone=SCHEDULER_TIMEZONE),
                id='daily_stats',
                name='Daily Statistics Report',
                replace_existing=True
            )
            logger.info(f"Scheduled daily stats report for 00:01 {SCHEDULER_TIMEZONE}")

            # Schedule daily recheck-all — hour configurable via Settings page
            from config import RECHECK_HOUR, CONTINUOUS_OFFLOAD_INTERVAL
            self.scheduler.add_job(
                self._recheck_all_background,
                trigger=CronTrigger(hour=RECHECK_HOUR, minute=0, timezone=SCHEDULER_TIMEZONE),
                id='daily_recheck',
                name='Daily Recheck-All',
                replace_existing=True
            )
            logger.info(f"Scheduled daily recheck-all for {RECHECK_HOUR:02d}:00 {SCHEDULER_TIMEZONE}")

            # Schedule continuous offload — runs every N minutes, processes recently updated tickets
            self.scheduler.add_job(
                self.continuous_offload_job,
                trigger=IntervalTrigger(minutes=CONTINUOUS_OFFLOAD_INTERVAL),
                id='continuous_offload',
                name=f'Continuous Offload (every {CONTINUOUS_OFFLOAD_INTERVAL}m)',
                replace_existing=True,
                next_run_time=datetime.now() + timedelta(seconds=30)  # stagger: 30s after boot
            )
            logger.info(f"Scheduled continuous offload every {CONTINUOUS_OFFLOAD_INTERVAL} minute(s)")

            # Schedule storage snapshot refresh
            from config import STORAGE_REPORT_INTERVAL
            self.scheduler.add_job(
                self.storage_snapshot_job,
                trigger=IntervalTrigger(minutes=STORAGE_REPORT_INTERVAL),
                id='storage_snapshot',
                name=f'Storage Snapshot (every {STORAGE_REPORT_INTERVAL}m)',
                replace_existing=True,
                next_run_time=datetime.now() + timedelta(minutes=3)  # stagger: 3min after boot (between offload cycles)
            )
            logger.info(f"Scheduled storage snapshot every {STORAGE_REPORT_INTERVAL} minute(s)")

            # ── Closed-ticket backup (separate job) ─────────────────────
            from config import TICKET_BACKUP_ENABLED, TICKET_BACKUP_INTERVAL_MINUTES
            if TICKET_BACKUP_ENABLED:
                self.scheduler.add_job(
                    self._ticket_backup_background,
                    trigger=IntervalTrigger(minutes=TICKET_BACKUP_INTERVAL_MINUTES),
                    id='closed_ticket_backup',
                    name=f'Closed Ticket Backup (every {TICKET_BACKUP_INTERVAL_MINUTES}m)',
                    replace_existing=True,
                    next_run_time=datetime.now() + timedelta(minutes=5),  # stagger 5min
                )
                logger.info(f"Scheduled closed-ticket backup every {TICKET_BACKUP_INTERVAL_MINUTES} minute(s)")
            else:
                logger.info("Closed-ticket backup is DISABLED")

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
            try:
                from config import reload_config, SCHEDULER_TIMEZONE as TZ, RECHECK_HOUR, CONTINUOUS_OFFLOAD_INTERVAL
                reload_config()
                import socket
                host = socket.gethostname()
                self.telegram_reporter.send_message(
                    f"▶️ <b>Scheduler started</b>\n🖥️ {host}\n"
                    f"⚡ Continuous offload: every {CONTINUOUS_OFFLOAD_INTERVAL} min\n"
                    f"🔄 Full offload: every 5 min\n"
                    f"🔁 Daily recheck: {RECHECK_HOUR:02d}:00 {TZ}"
                )
            except Exception:
                pass

        except Exception as e:
            logger.error(f"Failed to start scheduler: {e}", exc_info=True)
            print(f"ERROR: Failed to start scheduler: {e}")
            raise

    def stop(self):
        """Stop the scheduler"""
        try:
            self.telegram_reporter.send_message("⏹ <b>Scheduler stopped</b>")
        except Exception:
            pass
        self.scheduler.shutdown()
    
    def run_now(self):
        """Manually trigger the offload job"""
        self.scheduled_job()

    def run_backup_now(self):
        """Manually trigger the backup job"""
        self.backup_job()

    def recheck_all_job(self):
        """Scan all tickets and re-process those with remaining attachments"""
        if getattr(self, '_recheck_running', False):
            logger.warning("Recheck-all job already running, skipping")
            return

        acquired = self._job_lock.acquire(blocking=False)
        if not acquired:
            logger.warning("Could not acquire job lock for recheck-all (another job is running)")
            return

        try:
            self._recheck_running = True
            self._recheck_started_at = datetime.utcnow()
            self._recheck_last_summary = None
            self._recheck_progress = {"current": 0, "total": 0, "current_ticket": None}
            logger.info(f"Recheck-all job started at {datetime.utcnow()}")

            def _progress(current, total, ticket_id):
                self._recheck_progress = {
                    "current": current,
                    "total": total,
                    "current_ticket": ticket_id,
                }

            summary = self.offloader.run_recheck_all_offload(progress_callback=_progress)
            self._recheck_last_summary = summary

            logger.info(
                f"Recheck-all complete — checked: {summary.get('tickets_scanned', 0)}/{summary.get('tickets_total', 0)}, "
                f"found: {summary.get('tickets_with_remaining_attachments', 0)}, "
                f"processed: {summary.get('tickets_processed', 0)}, "
                f"uploaded: {summary.get('attachments_uploaded', 0)}, "
                f"empty: {summary.get('tickets_genuinely_empty', 0)}, "
                f"404: {summary.get('tickets_404', 0)}, "
                f"errors: {len(summary.get('errors', []))}"
            )
        except Exception as e:
            logger.error(f"Error in recheck-all job: {e}", exc_info=True)
        finally:
            self._recheck_running = False
            self._job_lock.release()

    def _recheck_all_background(self):
        """APScheduler entry point — runs recheck_all_job in a daemon thread so the
        scheduler thread pool is never blocked by a long-running scan."""
        t = threading.Thread(target=self.recheck_all_job, daemon=True, name='recheck-all')
        t.start()

    def run_recheck_all_now(self):
        """Manually trigger the recheck-all job (non-blocking — runs in background thread)."""
        self._recheck_all_background()

    def get_recheck_status(self):
        """Return the current recheck-all status and last summary for the API."""
        running = getattr(self, '_recheck_running', False)
        started_at = getattr(self, '_recheck_started_at', None)
        summary = getattr(self, '_recheck_last_summary', None)
        progress = getattr(self, '_recheck_progress', {"current": 0, "total": 0, "current_ticket": None})
        # Expose next scheduled run time
        next_run = None
        try:
            if self.scheduler.running:
                job = self.scheduler.get_job('daily_recheck')
                if job and job.next_run_time:
                    next_run = job.next_run_time.isoformat()
        except Exception:
            pass
        # Fallback: compute next daily occurrence from config
        if not next_run:
            try:
                from config import RECHECK_HOUR, SCHEDULER_TIMEZONE
                import pytz
                from datetime import timedelta
                tz = pytz.timezone(SCHEDULER_TIMEZONE)
                now = datetime.now(tz)
                next_dt = now.replace(hour=RECHECK_HOUR, minute=0, second=0, microsecond=0)
                if next_dt <= now:
                    next_dt += timedelta(days=1)  # already past today's run — show tomorrow
                next_run = next_dt.isoformat()
            except Exception:
                pass
        return {
            "running": running,
            "started_at": started_at.isoformat() if started_at else None,
            "progress": progress,
            "summary": summary,
            "next_scheduled_run": next_run,
        }

    # ── Closed-ticket backup ───────────────────────────────────────────
    def ticket_backup_job(self):
        """Run the closed-ticket backup and send summary to Telegram/Slack."""
        if self._ticket_backup_running:
            logger.debug("[TicketBackup] Previous run still active — skipping")
            return
        acquired = self._ticket_backup_lock.acquire(blocking=False)
        if not acquired:
            return
        try:
            self._ticket_backup_running = True
            self._ticket_backup_started_at = datetime.utcnow()
            logger.info("[TicketBackup] Starting closed-ticket backup job")

            from config import TICKET_BACKUP_DAILY_LIMIT
            limit = TICKET_BACKUP_DAILY_LIMIT or 0

            stats = self.ticket_backup_manager.backup_closed_tickets(limit=limit)
            self._ticket_backup_last_summary = stats

            # ── Build summary message ──────────────────────────
            scanned = stats.get('tickets_scanned', 0)
            backed = stats.get('tickets_backed_up', 0)
            files = stats.get('files_uploaded', 0)
            bts = stats.get('bytes_uploaded', 0)
            errs = stats.get('errors', [])
            mb = bts / 1048576

            logger.info(
                f"[TicketBackup] Completed — scanned={scanned}, backed_up={backed}, "
                f"files={files}, bytes={bts}, errors={len(errs)}"
            )
        except Exception as e:
            logger.error(f"[TicketBackup] Error: {e}", exc_info=True)
            try:
                self.telegram_reporter.send_message(f"❌ <b>Closed-Ticket Backup Failed</b>\n\n{e}")
            except Exception:
                pass
        finally:
            self._ticket_backup_running = False
            self._ticket_backup_lock.release()

    def _ticket_backup_background(self):
        """APScheduler entry — run in daemon thread so scheduler pool isn't blocked."""
        t = threading.Thread(target=self.ticket_backup_job, daemon=True, name='ticket-backup')
        t.start()

    def run_ticket_backup_now(self):
        """Manually trigger the closed-ticket backup (non-blocking)."""
        self._ticket_backup_background()

    def get_ticket_backup_status(self):
        """Return current ticket-backup job status for the API."""
        running = getattr(self, '_ticket_backup_running', False)
        started_at = getattr(self, '_ticket_backup_started_at', None)
        summary = getattr(self, '_ticket_backup_last_summary', None)
        next_run = None
        try:
            if self.scheduler.running:
                job = self.scheduler.get_job('closed_ticket_backup')
                if job and job.next_run_time:
                    next_run = job.next_run_time.isoformat()
        except Exception:
            pass
        return {
            "running": running,
            "started_at": started_at.isoformat() if started_at else None,
            "summary": summary,
            "next_scheduled_run": next_run,
        }



