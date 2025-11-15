"""
Telegram reporting functionality
"""
import requests
from datetime import datetime
from typing import Dict, Optional
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

class TelegramReporter:
    """Send reports to Telegram"""
    
    def __init__(self, bot_token: Optional[str] = None, chat_id: Optional[str] = None):
        self.bot_token = bot_token or TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or TELEGRAM_CHAT_ID
        self.api_url = None
        if self.bot_token:
            self.api_url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
    
    def send_report(self, summary: Dict) -> bool:
        """
        Send report to Telegram
        """
        if not self.bot_token or not self.chat_id:
            print("Telegram bot token or chat ID not configured")
            return False
        
        try:
            message = self._format_report(summary)
            
            payload = {
                "chat_id": self.chat_id,
                "text": message,
                "parse_mode": "HTML"
            }
            
            response = requests.post(self.api_url, json=payload, timeout=10)
            response.raise_for_status()
            
            return True
        except Exception as e:
            print(f"Error sending Telegram report: {e}")
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

