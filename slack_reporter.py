"""
Slack reporting functionality
"""
import requests
import json
import logging
from datetime import datetime
from typing import Dict, Optional
from config import SLACK_WEBHOOK_URL

# Get logger
logger = logging.getLogger('zendesk_offloader')

class SlackReporter:
    """Send reports to Slack"""
    
    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url or SLACK_WEBHOOK_URL
    
    def send_report(self, summary: Dict) -> bool:
        """
        Send report to Slack
        """
        if not self.webhook_url:
            print("Slack webhook URL not configured")
            logger.warning("Slack webhook URL not configured")
            return False
        
        try:
            payload = self._format_report(summary)
            
            # Log the full report being sent (formatted as JSON for readability)
            logger.info("Sending Slack report:")
            logger.info(json.dumps(payload, indent=2, default=str))
            
            response = requests.post(
                self.webhook_url,
                json=payload,
                headers={'Content-Type': 'application/json'},
                timeout=10
            )
            response.raise_for_status()
            
            logger.info("Slack report sent successfully")
            return True
        except Exception as e:
            error_msg = f"Error sending Slack report: {e}"
            print(error_msg)
            logger.error(error_msg)
            return False
    
    def _format_report(self, summary: Dict) -> Dict:
        """Format report as Slack message"""
        run_date = summary['run_date']
        if isinstance(run_date, str):
            run_date_str = run_date
        else:
            run_date_str = run_date.strftime('%Y-%m-%d %H:%M:%S') if hasattr(run_date, 'strftime') else str(run_date)
        
        # Determine color and status
        has_errors = len(summary.get('errors', [])) > 0
        color = "#36a64f" if not has_errors else "#ff9900"  # Green for success, Orange for warnings
        
        # Build fields
        fields = [
            {
                "title": "Tickets Found",
                "value": str(summary.get('tickets_found', 0)),
                "short": True
            },
            {
                "title": "Tickets Processed",
                "value": str(summary['tickets_processed']),
                "short": True
            },
            {
                "title": "Attachments Uploaded",
                "value": str(summary['attachments_uploaded']),
                "short": True
            },
            {
                "title": "Errors",
                "value": str(len(summary.get('errors', []))),
                "short": True
            }
        ]
        
        if summary.get('attachments_deleted', 0) > 0:
            fields.append({
                "title": "Attachments Deleted",
                "value": str(summary.get('attachments_deleted', 0)),
                "short": True
            })
        
        # Build attachment
        attachment = {
            "color": color,
            "title": "Zendesk to Wasabi B2 Offload Report",
            "fields": fields,
            "footer": "Zendesk Offloader",
            "ts": int(datetime.utcnow().timestamp())
        }
        
        # Add error details if any
        if summary.get('errors'):
            error_text = "\n".join([f"• {error}" for error in summary['errors'][:10]])
            if len(summary['errors']) > 10:
                error_text += f"\n... and {len(summary['errors']) - 10} more errors"
            attachment["fields"].append({
                "title": "Error Details",
                "value": error_text,
                "short": False
            })
        
        # Add ticket details
        if summary.get('details'):
            details_text = "\n".join([
                f"• Ticket #{detail.get('ticket_id', 'N/A')}: {detail.get('attachments_uploaded', 0)} attachments"
                for detail in summary['details'][:5]
            ])
            if len(summary['details']) > 5:
                details_text += f"\n... and {len(summary['details']) - 5} more tickets"
            attachment["fields"].append({
                "title": "Ticket Details",
                "value": details_text,
                "short": False
            })
        
        payload = {
            "text": f"Zendesk Offload Report - {run_date_str} UTC",
            "attachments": [attachment]
        }
        
        return payload

