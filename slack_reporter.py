"""
Slack reporting functionality
"""
import requests
import json
import logging
from datetime import datetime
from typing import Dict, Optional
from pathlib import Path
from config import SLACK_WEBHOOK_URL
import os

# Get logger
logger = logging.getLogger('zendesk_offloader')

class SlackReporter:
    """Send reports to Slack"""
    
    def __init__(self, webhook_url: Optional[str] = None, bot_token: Optional[str] = None):
        self.webhook_url = webhook_url or SLACK_WEBHOOK_URL
        # For file uploads, we need a bot token with files:write scope
        self.bot_token = bot_token or os.getenv("SLACK_BOT_TOKEN", "")
    
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

        inlines_up = summary.get('inlines_uploaded', 0)
        inlines_del = summary.get('inlines_deleted', 0)
        if inlines_up > 0:
            fields.append({"title": "Inline Uploaded", "value": str(inlines_up), "short": True})
        if inlines_del > 0:
            fields.append({"title": "Inline Deleted", "value": str(inlines_del), "short": True})

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
            fields.append({
                "title": "Job Offload Size",
                "value": job_size_str,
                "short": True
            })

        # ── Zendesk Account Storage (from summary) ───────────────────────
        zs = summary.get('zendesk_storage') or {}
        if zs and not zs.get('error'):
            zd_used_gb = zs.get('zd_used_gb', 0)
            plan_limit_gb = zs.get('plan_limit_gb', 0)
            if zd_used_gb > 0 or plan_limit_gb > 0:
                if zd_used_gb >= 1.0:
                    used_str = f"{zd_used_gb:.2f} GB"
                else:
                    zd_used_mb = zs.get('zd_used_bytes', 0) / (1024 * 1024)
                    used_str = f"{zd_used_mb:.1f} MB"
                if plan_limit_gb > 0:
                    remaining = zs.get('remaining_gb', 0)
                    pct = min(zd_used_gb / plan_limit_gb * 100, 100)
                    used_str += f" / {plan_limit_gb:g} GB ({pct:.1f}%)"
                    fields.append({"title": "Zendesk File Storage", "value": used_str, "short": True})
                    fields.append({"title": "Remaining", "value": f"{remaining:.2f} GB", "short": True})
                else:
                    fields.append({"title": "Zendesk File Storage", "value": used_str, "short": True})

        
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

    def send_file(self, file_path: Path, caption: Optional[str] = None, channels: Optional[str] = None) -> bool:
        """
        Send a file to Slack using the files.upload API
        
        Note: This requires a Slack Bot Token with files:write scope in SLACK_BOT_TOKEN env var.
        If not configured, the file won't be sent but won't cause the backup to fail.
        
        Args:
            file_path: Path to the file to send
            caption: Optional caption/comment for the file
            channels: Comma-separated channel IDs (if not provided, file is private)
            
        Returns:
            True if successful, False otherwise
        """
        if not self.bot_token:
            logger.warning("Slack bot token not configured - cannot send file. Set SLACK_BOT_TOKEN env var.")
            logger.info("To enable Slack file uploads:")
            logger.info("1. Create a Slack App at https://api.slack.com/apps")
            logger.info("2. Add 'files:write' OAuth scope")
            logger.info("3. Install app to workspace and get Bot User OAuth Token")
            logger.info("4. Set SLACK_BOT_TOKEN environment variable")
            return False
        
        if not file_path.exists():
            logger.error(f"File not found: {file_path}")
            return False
        
        try:
            logger.info(f"Sending file to Slack: {file_path.name}")
            
            with open(file_path, 'rb') as f:
                files = {
                    'file': (file_path.name, f)
                }
                
                data = {
                    'filename': file_path.name,
                    'title': file_path.name
                }
                
                if caption:
                    data['initial_comment'] = caption
                
                if channels:
                    data['channels'] = channels
                
                headers = {
                    'Authorization': f'Bearer {self.bot_token}'
                }
                
                response = requests.post(
                    'https://slack.com/api/files.upload',
                    headers=headers,
                    data=data,
                    files=files,
                    timeout=300  # 5 minute timeout for large files
                )
                
                response.raise_for_status()
                result = response.json()
                
                if result.get('ok'):
                    logger.info(f"File sent to Slack successfully: {file_path.name}")
                    return True
                else:
                    error = result.get('error', 'Unknown error')
                    logger.error(f"Slack API error: {error}")
                    return False
                    
        except requests.exceptions.HTTPError as e:
            error_msg = f"HTTP error sending file to Slack: {e}"
            if hasattr(e.response, 'text'):
                error_msg += f" - Response: {e.response.text}"
            logger.error(error_msg)
            return False
        except requests.exceptions.RequestException as e:
            logger.error(f"Request error sending file to Slack: {e}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error sending file to Slack: {e}")
            return False


