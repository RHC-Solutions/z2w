"""
Telegram reporting functionality
"""
import requests
import logging
from datetime import datetime
from typing import Dict, Optional
from pathlib import Path
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

# Get logger
logger = logging.getLogger('zendesk_offloader')

class TelegramReporter:
    """Send reports to Telegram"""
    
    def __init__(self, bot_token: Optional[str] = None, chat_id: Optional[str] = None):
        self.bot_token = bot_token or TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or TELEGRAM_CHAT_ID
        self.api_url = None
        self.file_api_url = None
        if self.bot_token:
            self.api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
            self.file_api_url = f"https://api.telegram.org/bot{self.bot_token}/sendDocument"
    
    def send_report(self, summary: Dict) -> bool:
        """
        Send report to Telegram
        """
        if not self.bot_token or not self.chat_id:
            print("Telegram bot token or chat ID not configured")
            logger.warning("Telegram bot token or chat ID not configured")
            return False
        
        try:
            message = self._format_report(summary)
            
            # Log the full report being sent
            logger.info("Sending Telegram report:")
            logger.info(message)
            
            payload = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "HTML"
            }
            
            response = requests.post(self.api_url, json=payload, timeout=10)
            response.raise_for_status()
            
            logger.info("Telegram report sent successfully")
            return True
        except Exception as e:
            error_msg = f"Error sending Telegram report: {e}"
            print(error_msg)
            logger.error(error_msg)
            return False
    
    def send_message(self, message: str) -> bool:
        """
        Send a custom message to Telegram
        """
        if not self.bot_token or not self.chat_id:
            print("Telegram bot token or chat ID not configured")
            return False
        
        if not self.api_url:
            print("Telegram API URL not initialized - bot token may be invalid")
            return False
        
        try:
            payload = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "HTML"
            }
            
            response = requests.post(self.api_url, json=payload, timeout=10)
            response.raise_for_status()
            
            return True
        except requests.exceptions.HTTPError as e:
            error_msg = f"HTTP error sending Telegram message: {e}"
            if hasattr(e.response, 'text'):
                error_msg += f" - Response: {e.response.text}"
            print(error_msg)
            return False
        except requests.exceptions.RequestException as e:
            print(f"Request error sending Telegram message: {e}")
            return False
        except Exception as e:
            print(f"Unexpected error sending Telegram message: {e}")
            return False
    
    def _format_report(self, summary: Dict) -> str:
        """Format report as Telegram message"""
        run_date = summary['run_date']
        if isinstance(run_date, str):
            run_date_str = run_date
        else:
            run_date_str = run_date.strftime('%Y-%m-%d %H:%M:%S') if hasattr(run_date, 'strftime') else str(run_date)

        # Determine status emoji
        status_emoji = "✅" if len(summary.get('errors', [])) == 0 else "⚠️"

        message = f"""
{status_emoji} <b>Zendesk to Wasabi B2 Offload Report</b>

📅 <b>Run Date:</b> {run_date_str} UTC

📊 <b>Summary:</b>
• Tickets Found: {summary.get('tickets_found', 0)}
• Tickets Processed: {summary['tickets_processed']}
• Attachments Uploaded: {summary['attachments_uploaded']}
• Errors: {len(summary.get('errors', []))}
"""

        if summary.get('attachments_deleted', 0) > 0:
            message += f"• Attachments Deleted: {summary.get('attachments_deleted', 0)}\n"

        inlines_up = summary.get('inlines_uploaded', 0)
        inlines_del = summary.get('inlines_deleted', 0)
        if inlines_up > 0 or inlines_del > 0:
            message += f"• Inline Uploaded: {inlines_up}\n"
            if inlines_del > 0:
                message += f"• Inline Deleted: {inlines_del}\n"

        # ── Job offload size (bytes moved this run) ────────────────────────
        total_job_bytes = sum(
            detail.get('total_size_bytes', 0)
            for detail in summary.get('details', [])
            if isinstance(detail, dict)
        )
        if total_job_bytes > 0:
            if total_job_bytes >= 1024 * 1024 * 1024:
                job_size_str = f"{total_job_bytes / (1024**3):.2f} GB"
            elif total_job_bytes >= 1024 * 1024:
                job_size_str = f"{total_job_bytes / (1024**2):.1f} MB"
            elif total_job_bytes >= 1024:
                job_size_str = f"{total_job_bytes / 1024:.1f} KB"
            else:
                job_size_str = f"{total_job_bytes:,} bytes"
            message += f"• Job Offload Size: {job_size_str}\n"

        # ── Wasabi storage stats ──────────────────────────────────────────
        ws = summary.get('wasabi_storage') or {}
        if ws and not ws.get('error'):
            obj_count = ws.get('object_count', 0)
            total_gb = ws.get('total_gb', 0.0)
            total_mb = ws.get('total_mb', 0.0)
            if total_gb >= 1.0:
                size_str = f"{total_gb:.2f} GB"
            else:
                size_str = f"{total_mb:.1f} MB"
            message += f"\n☁️ <b>Wasabi Storage:</b>\n"
            message += f"• Objects: {obj_count:,}\n"
            message += f"• Used: {size_str}\n"
        elif ws.get('error'):
            message += f"\n☁️ <b>Wasabi Storage:</b> ⚠️ Could not fetch ({ws['error'][:80]})\n"

        # ── Zendesk Account Storage (from snapshot + plan limit) ─────────
        zs = summary.get('zendesk_storage') or {}
        if zs and not zs.get('error'):
            zd_used_gb = zs.get('zd_used_gb', 0)
            plan_limit_gb = zs.get('plan_limit_gb', 0)
            remaining_gb = zs.get('remaining_gb', 0)
            snap_count = zs.get('snap_ticket_count', 0)
            snap_with_files = zs.get('snap_with_files', 0)

            if zd_used_gb > 0 or plan_limit_gb > 0:
                message += f"\n📦 <b>Zendesk File Storage:</b>\n"
                # Used
                if zd_used_gb >= 1.0:
                    used_str = f"{zd_used_gb:.2f} GB"
                else:
                    zd_used_mb = zs.get('zd_used_bytes', 0) / (1024 * 1024)
                    used_str = f"{zd_used_mb:.1f} MB"
                message += f"• Used: {used_str}"
                if plan_limit_gb > 0:
                    message += f" / {plan_limit_gb:g} GB"
                    # Remaining
                    message += f"\n• Remaining: {remaining_gb:.2f} GB"
                    # Percentage bar
                    pct = min(zd_used_gb / plan_limit_gb * 100, 100) if plan_limit_gb else 0
                    filled = round(pct / 10)
                    bar = '▓' * filled + '░' * (10 - filled)
                    message += f"\n• {bar} {pct:.1f}%"
                message += f"\n• Tickets scanned: {snap_count:,}"
                if snap_with_files:
                    message += f" ({snap_with_files:,} with attachments)"
                message += "\n"

            # Offloaded
            freed_gb = zs.get('offloaded_gb', 0.0)
            freed_mb = zs.get('offloaded_mb', 0.0)
            if freed_gb >= 1.0:
                freed_str = f"{freed_gb:.2f} GB"
            elif freed_mb >= 0.1:
                freed_str = f"{freed_mb:.1f} MB"
            else:
                freed_str = f"{zs.get('offloaded_bytes', 0):,} bytes"
            offloaded_tkts = zs.get('offloaded_tickets', 0)
            tickets_total = zs.get('tickets_with_files', 0)
            message += f"\n📁 <b>Zendesk Storage Freed:</b>\n"
            message += f"• Offloaded to Wasabi: {freed_str}\n"
            message += f"• Tickets offloaded: {offloaded_tkts:,}"
            if tickets_total > offloaded_tkts:
                message += f" / {tickets_total:,} with files"
            message += "\n"
        elif zs.get('error'):
            message += f"\n📁 <b>Zendesk Storage:</b> ⚠️ Stats unavailable\n"

        # ── Ticket cache sync stats ───────────────────────────────────────
        cs = summary.get('cache_stats') or {}
        if cs.get('fetched'):
            message += f"\n🗄️ <b>Ticket Cache:</b> {cs['fetched']:,} synced " \
                       f"(+{cs.get('inserted', 0)} new, ↻{cs.get('updated', 0)} updated)\n"
        
        if summary.get('errors'):
            message += "\n❌ <b>Errors:</b>\n"
            for error in summary['errors'][:10]:  # Limit to first 10 errors
                message += f"• {error}\n"
            if len(summary['errors']) > 10:
                message += f"... and {len(summary['errors']) - 10} more errors\n"
        
        if summary.get('details'):
            message += "\n📋 <b>Ticket Details:</b>\n"
            for detail in summary['details'][:5]:  # Limit to first 5 tickets
                ticket_id = detail.get('ticket_id', 'N/A')
                attachments = detail.get('attachments_uploaded', 0)
                errors = len(detail.get('errors', []))
                error_indicator = " ❌" if errors > 0 else ""
                message += f"• Ticket #{ticket_id}: {attachments} attachments{error_indicator}\n"
            if len(summary['details']) > 5:
                message += f"... and {len(summary['details']) - 5} more tickets\n"
        
        return message.strip()

    def send_file(self, file_path: Path, caption: Optional[str] = None) -> bool:
        """
        Send a file to Telegram
        
        Args:
            file_path: Path to the file to send
            caption: Optional caption for the file
            
        Returns:
            True if successful, False otherwise
        """
        if not self.bot_token or not self.chat_id:
            logger.warning("Telegram bot token or chat ID not configured")
            return False
        
        if not self.file_api_url:
            logger.warning("Telegram file API URL not initialized")
            return False
        
        if not file_path.exists():
            logger.error(f"File not found: {file_path}")
            return False
        
        try:
            logger.info(f"Sending file to Telegram: {file_path.name}")
            
            with open(file_path, 'rb') as f:
                files = {
                    'document': (file_path.name, f)
                }
                
                data = {
                    'chat_id': self.chat_id
                }
                
                if caption:
                    data['caption'] = caption
                
                response = requests.post(
                    self.file_api_url,
                    data=data,
                    files=files,
                    timeout=300  # 5 minute timeout for large files
                )
                
                response.raise_for_status()
                
                logger.info(f"File sent to Telegram successfully: {file_path.name}")
                return True
                
        except requests.exceptions.HTTPError as e:
            error_msg = f"HTTP error sending file to Telegram: {e}"
            if hasattr(e.response, 'text'):
                error_msg += f" - Response: {e.response.text}"
            logger.error(error_msg)
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error sending file to Telegram: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error sending file to Telegram: {e}")
            return False


