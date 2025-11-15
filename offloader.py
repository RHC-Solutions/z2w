"""
Main offload logic for processing tickets and uploading attachments
"""
from datetime import datetime
from typing import Dict, List
import json
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
            "attachments_deleted": 0,
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
                    summary["attachments_deleted"] += result.get("attachments_deleted", 0)
                    summary["details"].append(result)
                    
                    # Extract S3 keys from uploaded files
                    s3_keys = [file_info["s3_key"] for file_info in result.get("uploaded_files", [])]
                    wasabi_files_json = json.dumps(s3_keys) if s3_keys else None
                    
                    # Mark ticket as processed in database
                    processed_ticket = ProcessedTicket(
                        ticket_id=ticket_id,
                        attachments_count=result["attachments_uploaded"],
                        status="processed",
                        wasabi_files=wasabi_files_json
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
        Process a single ticket and upload its attachments and inline images
        After uploading to Wasabi, replaces attachments/images in Zendesk with Wasabi links and deletes them
        """
        result = {
            "ticket_id": ticket_id,
            "attachments_uploaded": 0,
            "attachments_deleted": 0,
            "uploaded_files": [],
            "errors": []
        }
        
        # Get attachments for this ticket (now includes comment_id)
        attachments = self.zendesk.get_ticket_attachments(ticket_id)
        print(f"Found {len(attachments)} regular attachments for ticket {ticket_id}")
        
        # Get inline images from comments
        inline_images = self.zendesk.get_inline_images(ticket_id)
        print(f"Found {len(inline_images)} inline images for ticket {ticket_id}")
        
        # Create a set of inline image attachment IDs to avoid processing them twice
        inline_attachment_ids = {img.get("attachment_id") for img in inline_images if img.get("attachment_id")}
        print(f"Inline image attachment IDs: {inline_attachment_ids}")
        
        # Process regular attachments (excluding inline images)
        for attachment in attachments:
            # Skip if this attachment is an inline image (will be processed separately)
            if attachment.get("id") in inline_attachment_ids:
                print(f"Skipping attachment {attachment.get('id')} - it's an inline image, will be processed separately")
                continue
            attachment_url = attachment.get("content_url")
            attachment_id = attachment.get("id")
            comment_id = attachment.get("comment_id")
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
                        
                        # Get Wasabi URL using the same method as in tickets view
                        # This generates presigned URL with query parameters (AWSAccessKeyId, Signature, Expires)
                        wasabi_url = self.wasabi.get_file_url(s3_key, expires_in=31536000)  # 1 year expiration
                        
                        if wasabi_url and attachment_id and comment_id:
                            # Replace attachment in comment with Wasabi link and delete it
                            success = self.zendesk.replace_attachment_in_comment(
                                ticket_id=ticket_id,
                                comment_id=comment_id,
                                attachment_id=attachment_id,
                                wasabi_url=wasabi_url,
                                filename=filename
                            )
                            
                            if success:
                                result["attachments_deleted"] += 1
                                print(f"Replaced attachment {filename} with Wasabi link and deleted from Zendesk")
                            else:
                                result["errors"].append(f"Failed to replace/delete attachment {filename} in Zendesk")
                        elif not wasabi_url:
                            result["errors"].append(f"Failed to generate Wasabi URL for {filename}")
                        else:
                            result["errors"].append(f"Missing attachment_id or comment_id for {filename}")
                    else:
                        result["errors"].append(f"Failed to upload {filename}")
                else:
                    result["errors"].append(f"Failed to download {filename}")
            
            except Exception as e:
                result["errors"].append(f"Error processing {filename}: {str(e)}")
        
        # Process inline images
        print(f"Found {len(inline_images)} inline images to process for ticket {ticket_id}")
        for inline_image in inline_images:
            attachment_url = inline_image.get("content_url")
            attachment_id = inline_image.get("attachment_id")
            comment_id = inline_image.get("comment_id")
            filename = inline_image.get("file_name", "inline_image.png")
            content_type = inline_image.get("content_type", "image/png")
            original_html = inline_image.get("original_html", "")
            
            if not attachment_url or not attachment_id:
                print(f"Skipping inline image: missing attachment_url or attachment_id. URL: {attachment_url}, ID: {attachment_id}")
                continue
            
            print(f"Processing inline image: {filename} (attachment_id: {attachment_id}, comment_id: {comment_id})")
            
            try:
                # Download inline image
                print(f"Downloading inline image from: {attachment_url}")
                image_data = self.zendesk.download_attachment(attachment_url)
                
                if image_data:
                    print(f"Downloaded {len(image_data)} bytes for inline image {filename}")
                    # Upload to Wasabi
                    print(f"Uploading inline image {filename} to Wasabi...")
                    s3_key = self.wasabi.upload_attachment(
                        ticket_id=ticket_id,
                        attachment_data=image_data,
                        original_filename=filename,
                        content_type=content_type
                    )
                    
                    if s3_key:
                        print(f"Successfully uploaded inline image {filename} to Wasabi: {s3_key}")
                        result["attachments_uploaded"] += 1
                        result["uploaded_files"].append({
                            "original": filename,
                            "s3_key": s3_key
                        })
                        
                        # Get Wasabi URL using the same method as in tickets view
                        wasabi_url = self.wasabi.get_file_url(s3_key, expires_in=31536000)  # 1 year expiration
                        
                        if wasabi_url and attachment_id and comment_id and original_html:
                            print(f"Replacing inline image {filename} in Zendesk comment {comment_id} with Wasabi link...")
                            # Replace inline image in comment with Wasabi link and delete it
                            success = self.zendesk.replace_inline_image_in_comment(
                                ticket_id=ticket_id,
                                comment_id=comment_id,
                                attachment_id=attachment_id,
                                wasabi_url=wasabi_url,
                                filename=filename,
                                original_html=original_html
                            )
                            
                            if success:
                                result["attachments_deleted"] += 1
                                print(f"✓ Successfully replaced inline image {filename} with Wasabi link and deleted from Zendesk")
                            else:
                                error_msg = f"Failed to replace/delete inline image {filename} in Zendesk"
                                result["errors"].append(error_msg)
                                print(f"✗ {error_msg}")
                        elif not wasabi_url:
                            error_msg = f"Failed to generate Wasabi URL for inline image {filename}"
                            result["errors"].append(error_msg)
                            print(f"✗ {error_msg}")
                        else:
                            error_msg = f"Missing required data for inline image {filename} (wasabi_url: {bool(wasabi_url)}, attachment_id: {attachment_id}, comment_id: {comment_id}, original_html: {bool(original_html)})"
                            result["errors"].append(error_msg)
                            print(f"✗ {error_msg}")
                    else:
                        error_msg = f"Failed to upload inline image {filename} to Wasabi"
                        result["errors"].append(error_msg)
                        print(f"✗ {error_msg}")
                else:
                    error_msg = f"Failed to download inline image {filename} from {attachment_url}"
                    result["errors"].append(error_msg)
                    print(f"✗ {error_msg}")
            
            except Exception as e:
                result["errors"].append(f"Error processing inline image {filename}: {str(e)}")
        
        return result
    
    def run_offload(self) -> Dict:
        """
        Run the complete offload process and log results
        """
        print(f"Starting offload process at {datetime.utcnow()}")
        
        # Process tickets
        summary = self.process_tickets()
        
        # Collect all S3 keys from all processed tickets
        all_s3_keys = []
        for ticket_detail in summary.get("details", []):
            if isinstance(ticket_detail, dict):
                for file_info in ticket_detail.get("uploaded_files", []):
                    if isinstance(file_info, dict) and "s3_key" in file_info:
                        all_s3_keys.append({
                            "ticket_id": ticket_detail.get("ticket_id"),
                            "s3_key": file_info["s3_key"],
                            "original_filename": file_info.get("original", "")
                        })
        
        # Add S3 keys to summary for easy access
        summary["all_s3_keys"] = all_s3_keys
        
        # Create log entry
        db = get_db()
        try:
            # Store summary as JSON for structured access
            # Convert datetime and other non-serializable objects
            def json_serializer(obj):
                """JSON serializer for objects not serializable by default json code"""
                if isinstance(obj, datetime):
                    return obj.isoformat()
                raise TypeError(f"Type {type(obj)} not serializable")
            
            summary_for_storage = {
                "run_date": summary["run_date"].isoformat() if isinstance(summary["run_date"], datetime) else str(summary["run_date"]),
                "tickets_processed": summary["tickets_processed"],
                "attachments_uploaded": summary["attachments_uploaded"],
                "errors": summary["errors"],
                "all_s3_keys": all_s3_keys,
                "details": summary.get("details", [])
            }
            
            log_entry = OffloadLog(
                run_date=summary["run_date"],
                tickets_processed=summary["tickets_processed"],
                attachments_uploaded=summary["attachments_uploaded"],
                errors_count=len(summary["errors"]),
                status="completed" if len(summary["errors"]) == 0 else "completed_with_errors",
                report_sent=False,
                details=json.dumps(summary_for_storage)
            )
            
            db.add(log_entry)
            db.commit()
            
            summary["log_id"] = log_entry.id
        finally:
            db.close()
        
        return summary


