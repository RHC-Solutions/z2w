"""
Main offload logic for processing tickets and uploading attachments
"""
from datetime import datetime
from typing import Dict, List
from zendesk_client import ZendeskClient
from wasabi_client import WasabiClient
from database import get_db, ProcessedTicket, OffloadLog
from sqlalchemy.exc import IntegrityError

class AttachmentOffloader:
    """Main class for offloading attachments from Zendesk to Wasabi"""
    
    def __init__(self):
        # Reload config to get latest settings from .env
        from config import reload_config
        reload_config()
        
        # Also check database for settings and update environment
        db = get_db()
        try:
            from database import Setting
            settings_list = db.query(Setting).all()
            import os
            for setting in settings_list:
                # Update environment variables with database values
                os.environ[setting.key] = setting.value or ""
            # Reload config again to pick up database settings
            reload_config()
        finally:
            db.close()
        
        self.zendesk = ZendeskClient()
        self.wasabi = WasabiClient()
    
    def get_processed_ticket_ids(self) -> set:
        """Get set of already processed ticket IDs"""
        db = get_db()
        try:
            processed = db.query(ProcessedTicket.ticket_id).all()
            return {ticket_id[0] for ticket_id in processed}
        finally:
            db.close()
    
    def process_tickets(self) -> Dict:
        """
        Process new tickets and offload their attachments
        Returns summary dictionary
        """
        summary = {
            "run_date": datetime.utcnow(),
            "tickets_processed": 0,
            "attachments_uploaded": 0,
            "errors": [],
            "details": [],
            "tickets_found": 0
        }
        
        try:
            print("=" * 50)
            print("Starting ticket processing...")
            print("=" * 50)
            
            # Get already processed ticket IDs
            processed_ids = self.get_processed_ticket_ids()
            print(f"Found {len(processed_ids)} already processed tickets")
            
            # Get new tickets
            try:
                new_tickets = self.zendesk.get_new_tickets(processed_ids)
                summary["tickets_found"] = len(new_tickets)
                print(f"Processing {len(new_tickets)} new tickets")
            except Exception as e:
                error_msg = f"Failed to fetch tickets from Zendesk: {str(e)}"
                print(f"ERROR: {error_msg}")
                summary["errors"].append(error_msg)
                return summary
            
            # Process each ticket
            for ticket in new_tickets:
                ticket_id = ticket.get("id")
                db = get_db()
                try:
                    result = self.process_ticket(ticket_id)
                    summary["tickets_processed"] += 1
                    summary["attachments_uploaded"] += result["attachments_uploaded"]
                    summary["details"].append(result)
                    
                    # Mark ticket as processed in database
                    processed_ticket = ProcessedTicket(
                        ticket_id=ticket_id,
                        attachments_count=result["attachments_uploaded"],
                        status="processed"
                    )
                    db.add(processed_ticket)
                    db.commit()
                    
                    # Mark ticket as read in Zendesk
                    self.zendesk.mark_ticket_as_read(ticket_id)
                    
                except Exception as e:
                    error_msg = f"Error processing ticket {ticket_id}: {str(e)}"
                    summary["errors"].append(error_msg)
                    
                    # Log failed ticket
                    processed_ticket = ProcessedTicket(
                        ticket_id=ticket_id,
                        attachments_count=0,
                        status="error",
                        error_message=str(e)
                    )
                    try:
                        db.add(processed_ticket)
                        db.commit()
                    except IntegrityError:
                        db.rollback()
                finally:
                    db.close()
        
        except Exception as e:
            error_msg = f"Critical error: {str(e)}"
            print(f"CRITICAL ERROR: {error_msg}")
            summary["errors"].append(error_msg)
        
        print("=" * 50)
        print(f"Processing complete. Processed: {summary['tickets_processed']}, Attachments: {summary['attachments_uploaded']}, Errors: {len(summary['errors'])}")
        print("=" * 50)
        return summary
    
    def process_ticket(self, ticket_id: int) -> Dict:
        """
        Process a single ticket and upload its attachments
        """
        result = {
            "ticket_id": ticket_id,
            "attachments_uploaded": 0,
            "uploaded_files": [],
            "errors": []
        }
        
        # Get attachments for this ticket
        attachments = self.zendesk.get_ticket_attachments(ticket_id)
        
        for attachment in attachments:
            attachment_url = attachment.get("content_url")
            filename = attachment.get("file_name", "unknown")
            content_type = attachment.get("content_type", "application/octet-stream")
            
            if not attachment_url:
                continue
            
            try:
                # Download attachment
                attachment_data = self.zendesk.download_attachment(attachment_url)
                
                if attachment_data:
                    # Upload to Wasabi
                    s3_key = self.wasabi.upload_attachment(
                        ticket_id=ticket_id,
                        attachment_data=attachment_data,
                        original_filename=filename,
                        content_type=content_type
                    )
                    
                    if s3_key:
                        result["attachments_uploaded"] += 1
                        result["uploaded_files"].append({
                            "original": filename,
                            "s3_key": s3_key
                        })
                    else:
                        result["errors"].append(f"Failed to upload {filename}")
                else:
                    result["errors"].append(f"Failed to download {filename}")
            
            except Exception as e:
                result["errors"].append(f"Error processing {filename}: {str(e)}")
        
        return result
    
    def run_offload(self) -> Dict:
        """
        Run the complete offload process and log results
        """
        print(f"Starting offload process at {datetime.utcnow()}")
        
        # Process tickets
        summary = self.process_tickets()
        
        # Create log entry
        db = get_db()
        try:
            log_entry = OffloadLog(
                run_date=summary["run_date"],
                tickets_processed=summary["tickets_processed"],
                attachments_uploaded=summary["attachments_uploaded"],
                errors_count=len(summary["errors"]),
                status="completed" if len(summary["errors"]) == 0 else "completed_with_errors",
                report_sent=False,
                details=str(summary)
            )
            
            db.add(log_entry)
            db.commit()
            
            summary["log_id"] = log_entry.id
        finally:
            db.close()
        
        return summary


