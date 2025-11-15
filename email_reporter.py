"""
Email reporting functionality
"""
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime
from typing import Dict
from config import SMTP_SERVER, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD, REPORT_EMAIL

class EmailReporter:
    """Send email reports"""
    
    def __init__(self):
        self.smtp_server = SMTP_SERVER
        self.smtp_port = SMTP_PORT
        self.smtp_username = SMTP_USERNAME
        self.smtp_password = SMTP_PASSWORD
        self.report_email = REPORT_EMAIL
    
    def send_report(self, summary: Dict) -> bool:
        """
        Send detailed report email
        """
        try:
            # Create message
            msg = MIMEMultipart()
            msg['From'] = self.smtp_username
            msg['To'] = self.report_email
            msg['Subject'] = f"Zendesk Offload Report - {summary['run_date'].strftime('%Y-%m-%d %H:%M:%S')} UTC"
            
            # Create email body
            body = self._format_report(summary)
            msg.attach(MIMEText(body, 'html'))
            
            # Send email
            with smtplib.SMTP(self.smtp_server, self.smtp_port) as server:
                server.starttls()
                server.login(self.smtp_username, self.smtp_password)
                server.send_message(msg)
            
            return True
        except Exception as e:
            print(f"Error sending email report: {e}")
            return False
    
    def _format_report(self, summary: Dict) -> str:
        """Format report as HTML"""
        html = f"""
        <html>
        <head>
            <style>
                body {{ font-family: Arial, sans-serif; }}
                h1 {{ color: #333; }}
                .summary {{ background-color: #f5f5f5; padding: 15px; border-radius: 5px; margin: 10px 0; }}
                .success {{ color: green; }}
                .error {{ color: red; }}
                table {{ border-collapse: collapse; width: 100%; margin: 10px 0; }}
                th, td {{ border: 1px solid #ddd; padding: 8px; text-align: left; }}
                th {{ background-color: #4CAF50; color: white; }}
            </style>
        </head>
        <body>
            <h1>Zendesk to Wasabi B2 Offload Report</h1>
            <p><strong>Run Date:</strong> {summary['run_date'].strftime('%Y-%m-%d %H:%M:%S')} UTC</p>
            
            <div class="summary">
                <h2>Summary</h2>
                <ul>
                    <li><strong>Tickets Found:</strong> {summary.get('tickets_found', 0)}</li>
                    <li><strong>Tickets Processed:</strong> {summary['tickets_processed']}</li>
                    <li><strong>Attachments Uploaded:</strong> {summary['attachments_uploaded']}</li>
                    <li><strong>Attachments Deleted:</strong> {summary.get('attachments_deleted', 0)}</li>
                    <li><strong>Errors:</strong> {len(summary['errors'])}</li>
                </ul>
            </div>
        """
        
        if summary.get('details'):
            html += """
            <h2>Ticket Details</h2>
            <table>
                <tr>
                    <th>Ticket ID</th>
                    <th>Attachments Uploaded</th>
                    <th>Files</th>
                    <th>Errors</th>
                </tr>
            """
            for detail in summary['details']:
                files_list = ", ".join([f["s3_key"] for f in detail.get("uploaded_files", [])])
                errors_list = ", ".join(detail.get("errors", [])) if detail.get("errors") else "None"
                
                html += f"""
                <tr>
                    <td>{detail['ticket_id']}</td>
                    <td>{detail['attachments_uploaded']}</td>
                    <td>{files_list or 'None'}</td>
                    <td class="{'error' if detail.get('errors') else ''}">{errors_list}</td>
                </tr>
                """
            html += "</table>"
        
        if summary.get('errors'):
            html += """
            <h2 class="error">Errors</h2>
            <ul>
            """
            for error in summary['errors']:
                html += f"<li class='error'>{error}</li>"
            html += "</ul>"
        
        html += """
        </body>
        </html>
        """
        return html


