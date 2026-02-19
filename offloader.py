"""
Main offload logic for processing tickets and uploading attachments
"""
from datetime import datetime
from typing import Dict, List, Optional, Callable
import json
import logging
from zendesk_client import ZendeskClient
from wasabi_client import WasabiClient
from database import get_db, ProcessedTicket, OffloadLog, ZendeskTicketCache
from sqlalchemy.exc import IntegrityError

# Get logger
logger = logging.getLogger('zendesk_offloader')

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

    def sync_ticket_cache(self, progress_callback: Optional[Callable] = None) -> dict:
        """
        Fetch all tickets from Zendesk and upsert them into the local
        ZendeskTicketCache table.  Returns a small stats dict.

        This is the single place where we pull the full list — daily runs
        call it so the recheck never needs a full API scan again.
        """
        stats = {"fetched": 0, "inserted": 0, "updated": 0, "errors": 0}
        logger.info("Syncing Zendesk ticket cache…")
        try:
            all_tickets = self.zendesk.get_all_tickets(status="all")
            stats["fetched"] = len(all_tickets)
            logger.info(f"Ticket cache sync: fetched {len(all_tickets)} tickets from Zendesk")

            db = get_db()
            try:
                now = datetime.utcnow()
                batch_size = 500
                for i, t in enumerate(all_tickets):
                    try:
                        tid = t.get("id")
                        if not tid:
                            continue

                        # Parse Zendesk ISO timestamps
                        def _parse_dt(s):
                            if not s:
                                return None
                            try:
                                return datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S")
                            except Exception:
                                return None

                        row = db.query(ZendeskTicketCache).filter_by(ticket_id=tid).first()
                        if row:
                            row.subject = t.get("subject") or ""
                            row.status = t.get("status")
                            row.updated_at = _parse_dt(t.get("updated_at"))
                            row.has_attachments = bool(t.get("has_incidents") or
                                                       t.get("via", {}).get("channel") == "email")
                            row.comment_count = t.get("comment_count")
                            row.requester_id = t.get("requester_id")
                            row.assignee_id = t.get("assignee_id")
                            row.tags = json.dumps(t.get("tags", []))
                            row.cached_at = now
                            stats["updated"] += 1
                        else:
                            db.add(ZendeskTicketCache(
                                ticket_id=tid,
                                subject=t.get("subject") or "",
                                status=t.get("status"),
                                created_at=_parse_dt(t.get("created_at")),
                                updated_at=_parse_dt(t.get("updated_at")),
                                has_attachments=bool(t.get("has_incidents") or
                                                     t.get("via", {}).get("channel") == "email"),
                                comment_count=t.get("comment_count"),
                                requester_id=t.get("requester_id"),
                                assignee_id=t.get("assignee_id"),
                                tags=json.dumps(t.get("tags", [])),
                                cached_at=now,
                            ))
                            stats["inserted"] += 1

                        # Commit in batches to avoid one huge transaction
                        if (i + 1) % batch_size == 0:
                            db.commit()
                            logger.info(f"Cache sync progress: {i + 1}/{len(all_tickets)}")
                            if progress_callback:
                                progress_callback(i + 1, len(all_tickets), tid)

                    except Exception as e:
                        stats["errors"] += 1
                        logger.warning(f"Cache sync error for ticket {t.get('id')}: {e}")
                        db.rollback()

                db.commit()
            finally:
                db.close()

        except Exception as e:
            logger.error(f"sync_ticket_cache failed: {e}", exc_info=True)
            stats["errors"] += 1

        logger.info(
            f"Ticket cache sync complete — fetched: {stats['fetched']}, "
            f"inserted: {stats['inserted']}, updated: {stats['updated']}, errors: {stats['errors']}"
        )
        return stats
    
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
            "inlines_uploaded": 0,
            "inlines_deleted": 0,
            "errors": [],
            "details": [],
            "tickets_found": 0
        }
        
        try:
            logger.info("=" * 50)
            logger.info("[process_tickets] Starting new-ticket processing...")
            logger.info("=" * 50)
            
            # Get already processed ticket IDs
            processed_ids = self.get_processed_ticket_ids()
            logger.info(f"[process_tickets] {len(processed_ids)} tickets already processed")
            
            # Get new tickets
            try:
                new_tickets = self.zendesk.get_new_tickets(processed_ids)
                summary["tickets_found"] = len(new_tickets)
                logger.info(f"[process_tickets] {len(new_tickets)} new ticket(s) to process")
            except Exception as e:
                error_msg = f"Failed to fetch tickets from Zendesk: {str(e)}"
                logger.error(f"[process_tickets] ERROR: {error_msg}")
                summary["errors"].append(error_msg)
                return summary
            
            # Process each ticket
            for ticket in new_tickets:
                ticket_id = ticket.get("id")
                db = get_db()
                try:
                    logger.info(f"[Ticket {ticket_id}] Starting processing...")
                    result = self.process_ticket(ticket_id)
                    summary["tickets_processed"] += 1
                    summary["attachments_uploaded"] += result["attachments_uploaded"]
                    summary["attachments_deleted"] += result.get("attachments_deleted", 0)
                    summary["inlines_uploaded"] += result.get("inlines_uploaded", 0)
                    summary["inlines_deleted"] += result.get("inlines_deleted", 0)
                    summary["details"].append(result)
                    logger.info(
                        f"[Ticket {ticket_id}] Done — uploaded: {result['attachments_uploaded']}, "
                        f"deleted: {result.get('attachments_deleted', 0)}, "
                        f"size: {result.get('total_size_bytes', 0):,} bytes, "
                        f"errors: {len(result.get('errors', []))}"
                    )
                    
                    # Extract S3 keys from uploaded files
                    s3_keys = [file_info["s3_key"] for file_info in result.get("uploaded_files", [])]
                    wasabi_files_json = json.dumps(s3_keys) if s3_keys else None
                    total_size_bytes = result.get("total_size_bytes", 0)
                    
                    # Mark ticket as processed in database
                    # Check if ticket already exists (in case of race condition)
                    existing = db.query(ProcessedTicket).filter_by(ticket_id=ticket_id).first()
                    if existing:
                        # Update existing record
                        existing.processed_at = datetime.utcnow()
                        existing.attachments_count = result["attachments_uploaded"]
                        existing.status = "processed"
                        existing.error_message = None
                        existing.wasabi_files = wasabi_files_json
                        existing.wasabi_files_size = total_size_bytes
                        db.commit()
                        logger.debug(f"[Ticket {ticket_id}] Updated existing DB record")
                    else:
                        # Create new record
                        processed_ticket = ProcessedTicket(
                            ticket_id=ticket_id,
                            attachments_count=result["attachments_uploaded"],
                            status="processed",
                            wasabi_files=wasabi_files_json,
                            wasabi_files_size=total_size_bytes
                        )
                        db.add(processed_ticket)
                        db.commit()
                    
                    # Mark ticket as read in Zendesk
                    self.zendesk.mark_ticket_as_read(ticket_id)
                    
                except Exception as e:
                    db.rollback()  # Rollback any pending transaction
                    error_msg = f"Error processing ticket {ticket_id}: {str(e)}"
                    summary["errors"].append(error_msg)
                    print(f"ERROR: {error_msg}")
                    logger.error(error_msg)
                    
                    # Log failed ticket - check if already exists first
                    try:
                        existing = db.query(ProcessedTicket).filter_by(ticket_id=ticket_id).first()
                        if existing:
                            # Update existing record with error
                            existing.processed_at = datetime.utcnow()
                            existing.status = "error"
                            existing.error_message = str(e)
                            db.commit()
                        else:
                            # Create new error record
                            processed_ticket = ProcessedTicket(
                                ticket_id=ticket_id,
                                attachments_count=0,
                                status="error",
                                error_message=str(e)
                            )
                            db.add(processed_ticket)
                            db.commit()
                    except Exception as db_error:
                        logger.warning(f"[Ticket {ticket_id}] Could not log error to DB: {db_error}")
                        db.rollback()
                finally:
                    db.close()
        
        except Exception as e:
            error_msg = f"Critical error: {str(e)}"
            logger.error(f"[process_tickets] CRITICAL: {error_msg}", exc_info=True)
            summary["errors"].append(error_msg)
        
        logger.info("=" * 50)
        logger.info(
            f"[process_tickets] Complete — processed: {summary['tickets_processed']}, "
            f"attachments uploaded: {summary['attachments_uploaded']}, "
            f"errors: {len(summary['errors'])}"
        )
        logger.info("=" * 50)
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
            "inlines_uploaded": 0,
            "inlines_deleted": 0,
            "uploaded_files": [],
            "total_size_bytes": 0,
            "errors": []
        }
        
        # Get attachments for this ticket (now includes comment_id)
        attachments = self.zendesk.get_ticket_attachments(ticket_id)
        
        # Get inline images from comments
        inline_images = self.zendesk.get_inline_images(ticket_id)
        
        # Create a set of inline image attachment IDs to avoid processing them twice
        inline_attachment_ids = {img.get("attachment_id") for img in inline_images if img.get("attachment_id")}
        logger.info(
            f"[Ticket {ticket_id}] Found {len(attachments)} regular attachment(s) and "
            f"{len(inline_images)} inline image(s)"
        )
        
        # Process regular attachments (excluding inline images)
        for attachment in attachments:
            # Skip if this attachment is an inline image (will be processed separately)
            if attachment.get("id") in inline_attachment_ids:
                logger.debug(f"[Ticket {ticket_id}] Skipping attachment {attachment.get('id')} (inline image — processed separately)")
                continue
            attachment_url = attachment.get("content_url")
            attachment_id = attachment.get("id")
            comment_id = attachment.get("comment_id")
            filename = attachment.get("file_name", "unknown")
            content_type = attachment.get("content_type", "application/octet-stream")

            # Always skip redacted placeholder files
            if filename.lower().endswith('redacted.txt'):
                logger.info(f"[Ticket {ticket_id}] Skipping already-redacted file: {filename}")
                continue

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
                        file_size = len(attachment_data)
                        result["attachments_uploaded"] += 1
                        result["total_size_bytes"] += file_size
                        result["uploaded_files"].append({
                            "original": filename,
                            "s3_key": s3_key,
                            "size_bytes": file_size
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
                                logger.info(f"[Ticket {ticket_id}] ✓ {filename} uploaded to Wasabi and replaced in Zendesk ({file_size:,} bytes)")
                            else:
                                logger.warning(f"[Ticket {ticket_id}] ✗ Failed to replace/delete {filename} in Zendesk after upload")
                                result["errors"].append(f"Failed to replace/delete attachment {filename} in Zendesk")
                        elif not wasabi_url:
                            logger.warning(f"[Ticket {ticket_id}] ✗ Could not generate Wasabi URL for {filename}")
                            result["errors"].append(f"Failed to generate Wasabi URL for {filename}")
                        else:
                            logger.warning(f"[Ticket {ticket_id}] ✗ Missing attachment_id or comment_id for {filename}")
                            result["errors"].append(f"Missing attachment_id or comment_id for {filename}")
                    else:
                        logger.warning(f"[Ticket {ticket_id}] ✗ Upload to Wasabi failed for {filename}")
                        result["errors"].append(f"Failed to upload {filename}")
                else:
                    logger.warning(f"[Ticket {ticket_id}] ✗ Download from Zendesk failed for {filename}")
                    result["errors"].append(f"Failed to download {filename}")
            
            except Exception as e:
                logger.error(f"[Ticket {ticket_id}] ✗ Exception processing attachment {filename}: {e}")
                result["errors"].append(f"Error processing {filename}: {str(e)}")
        
        # Process inline images
        for inline_image in inline_images:
            attachment_url = inline_image.get("content_url")
            attachment_id = inline_image.get("attachment_id")
            comment_id = inline_image.get("comment_id")
            filename = inline_image.get("file_name", "inline_image.png")
            content_type = inline_image.get("content_type", "image/png")
            original_html = inline_image.get("original_html", "")

            # Always skip redacted placeholder files
            if filename.lower().endswith('redacted.txt'):
                logger.info(f"[Ticket {ticket_id}] Skipping already-redacted inline file: {filename}")
                continue

            if not attachment_url:
                logger.debug(f"[Ticket {ticket_id}] Skipping inline image {filename}: no attachment_url (id={attachment_id})")
                continue
            
            logger.info(f"[Ticket {ticket_id}] Processing inline image: {filename} (attachment_id={attachment_id}, comment_id={comment_id})")
            
            try:
                # Download inline image
                image_data = self.zendesk.download_attachment(attachment_url)
                
                if image_data:
                    # Upload to Wasabi
                    s3_key = self.wasabi.upload_attachment(
                        ticket_id=ticket_id,
                        attachment_data=image_data,
                        original_filename=filename,
                        content_type=content_type
                    )
                    
                    if s3_key:
                        image_size = len(image_data)
                        result["attachments_uploaded"] += 1
                        result["inlines_uploaded"] += 1
                        result["total_size_bytes"] += image_size
                        result["uploaded_files"].append({
                            "original": filename,
                            "s3_key": s3_key,
                            "size_bytes": image_size,
                            "is_inline": True
                        })
                        logger.info(f"[Ticket {ticket_id}] ✓ Uploaded inline image {filename} ({image_size:,} bytes) → {s3_key}")
                        # Get Wasabi URL using the same method as in tickets view
                        wasabi_url = self.wasabi.get_file_url(s3_key, expires_in=31536000)  # 1 year expiration
                        
                        if not wasabi_url:
                            logger.warning(f"[Ticket {ticket_id}] ✗ Could not generate Wasabi URL for inline image {filename}")
                            result["errors"].append(f"Failed to generate Wasabi URL for inline image {filename}")
                        elif attachment_id and comment_id and original_html:
                            # Full path: has attachment_id → replace img + redact
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
                                result["inlines_deleted"] += 1
                                logger.info(f"[Ticket {ticket_id}] ✓ Replaced inline image {filename} in Zendesk with Wasabi link")
                            else:
                                logger.warning(f"[Ticket {ticket_id}] ✗ Failed to replace/delete inline image {filename} in Zendesk")
                                result["errors"].append(f"Failed to replace/delete inline image {filename} in Zendesk")
                        elif comment_id:
                            # Token-URL-only image (no attachment_id) — add link comment on non-closed tickets
                            ticket_status = self.zendesk.get_ticket_status(ticket_id)
                            if ticket_status != "closed":
                                img_src = inline_image.get("img_src", attachment_url)
                                link_success = self.zendesk.add_wasabi_link_comment(
                                    ticket_id=ticket_id,
                                    comment_id=comment_id,
                                    wasabi_url=wasabi_url,
                                    filename=filename,
                                    img_src=img_src
                                )
                                if link_success:
                                    logger.info(f"[Ticket {ticket_id}] ✓ Added Wasabi link comment for token-URL image {filename} (no redact possible)")
                                else:
                                    logger.warning(f"[Ticket {ticket_id}] ✗ Failed to add Wasabi link comment for {filename}")
                                    result["errors"].append(f"Failed to add Wasabi link comment for {filename}")
                            else:
                                logger.info(f"[Ticket {ticket_id}] ⚠ Closed ticket — uploaded inline image {filename} to Wasabi only (cannot modify Zendesk)")
                    else:
                        logger.warning(f"[Ticket {ticket_id}] ✗ Upload to Wasabi failed for inline image {filename}")
                        result["errors"].append(f"Failed to upload inline image {filename} to Wasabi")
                else:
                    logger.warning(f"[Ticket {ticket_id}] ✗ Download failed for inline image {filename}")
                    result["errors"].append(f"Failed to download inline image {filename} from {attachment_url}")
            
            except Exception as e:
                logger.error(f"[Ticket {ticket_id}] ✗ Exception processing inline image {filename}: {e}")
                result["errors"].append(f"Error processing inline image {filename}: {str(e)}")
        
        return result
    
    def get_zendesk_storage_stats(self) -> dict:
        """
        Compute Zendesk-side storage statistics from the local database.

        Returns how many bytes we have offloaded to Wasabi (= freed from Zendesk)
        and how many tickets/files that covers.  Because Zendesk exposes no
        "total storage used" API endpoint, we derive what we can from our own
        records.
        """
        from sqlalchemy import func as sql_func
        db = get_db()
        try:
            # Total bytes offloaded (only rows where we tracked sizes, i.e. wasabi_files_size > 0)
            offloaded_bytes = db.query(
                sql_func.sum(ProcessedTicket.wasabi_files_size)
            ).scalar() or 0

            # Count of tickets where we have actual size data
            tickets_with_sizes = db.query(ProcessedTicket).filter(
                ProcessedTicket.wasabi_files_size > 0
            ).count()

            # Total processed tickets that had attachments
            tickets_with_files = db.query(ProcessedTicket).filter(
                ProcessedTicket.attachments_count > 0
            ).count()

            # Grand total of processed tickets
            total_processed = db.query(ProcessedTicket).count()

            offloaded_mb = offloaded_bytes / (1024 * 1024)
            offloaded_gb = offloaded_bytes / (1024 * 1024 * 1024)

            return {
                "offloaded_bytes": offloaded_bytes,
                "offloaded_mb": round(offloaded_mb, 2),
                "offloaded_gb": round(offloaded_gb, 3),
                "tickets_with_sizes": tickets_with_sizes,
                "tickets_with_files": tickets_with_files,
                "total_processed": total_processed,
            }
        finally:
            db.close()

    def process_recent_tickets(self, since_minutes: int = 10) -> Dict:
        """
        Lightweight continuous offload: fetch only tickets updated in the last
        `since_minutes` window, skip already-processed ones, upload attachments.
        Used by the interval scheduler — no full scan, no report email.
        Returns a small stats dict.
        """
        stats = {
            "checked": 0,
            "already_done": 0,
            "newly_processed": 0,
            "attachments_uploaded": 0,
            "total_size_bytes": 0,
            "errors": [],
        }

        try:
            recent_tickets = self.zendesk.get_recently_updated_tickets(since_minutes=since_minutes)
            stats["checked"] = len(recent_tickets)

            if not recent_tickets:
                return stats

            processed_ids = self.get_processed_ticket_ids()

            for ticket in recent_tickets:
                ticket_id = ticket.get("id")
                if not ticket_id:
                    continue

                if ticket_id in processed_ids:
                    stats["already_done"] += 1
                    continue

                db = get_db()
                try:
                    result = self.process_ticket(ticket_id)
                    uploaded = result.get("attachments_uploaded", 0)
                    size = result.get("total_size_bytes", 0)
                    errors = result.get("errors", [])

                    s3_keys = [f["s3_key"] for f in result.get("uploaded_files", [])]
                    wasabi_files_json = json.dumps(s3_keys) if s3_keys else None

                    existing = db.query(ProcessedTicket).filter_by(ticket_id=ticket_id).first()
                    if existing:
                        existing.processed_at = datetime.utcnow()
                        existing.attachments_count = uploaded
                        existing.status = "processed"
                        existing.error_message = None if not errors else "; ".join(str(e) for e in errors[:3])
                        if wasabi_files_json:
                            existing.wasabi_files = wasabi_files_json
                        existing.wasabi_files_size = size
                    else:
                        db.add(ProcessedTicket(
                            ticket_id=ticket_id,
                            attachments_count=uploaded,
                            status="processed",
                            error_message=None if not errors else "; ".join(str(e) for e in errors[:3]),
                            wasabi_files=wasabi_files_json,
                            wasabi_files_size=size,
                        ))
                    db.commit()

                    stats["newly_processed"] += 1
                    stats["attachments_uploaded"] += uploaded
                    stats["total_size_bytes"] += size

                    if uploaded > 0:
                        logger.info(
                            f"[Continuous] Ticket {ticket_id}: uploaded {uploaded} file(s) "
                            f"({size/1024/1024:.1f} MB)"
                        )

                except Exception as e:
                    db.rollback()
                    err = f"Ticket {ticket_id}: {e}"
                    stats["errors"].append(err)
                    logger.error(f"[Continuous offload] {err}", exc_info=True)
                    try:
                        existing = db.query(ProcessedTicket).filter_by(ticket_id=ticket_id).first()
                        if existing:
                            existing.status = "error"
                            existing.error_message = str(e)
                        else:
                            db.add(ProcessedTicket(ticket_id=ticket_id, attachments_count=0,
                                                   status="error", error_message=str(e)))
                        db.commit()
                    except Exception:
                        db.rollback()
                finally:
                    db.close()

        except Exception as e:
            logger.error(f"[Continuous offload] Fatal: {e}", exc_info=True)
            stats["errors"].append(str(e))

        return stats

    def run_offload(self) -> Dict:
        """
        Run the complete offload process and log results.
        Also syncs the local Zendesk ticket cache and collects Wasabi storage stats.
        """
        print(f"Starting offload process at {datetime.utcnow()}")

        # ── Sync local ticket cache (incremental — fast on subsequent runs) ──
        try:
            cache_stats = self.sync_ticket_cache()
            logger.info(f"Ticket cache sync: {cache_stats}")
        except Exception as e:
            logger.warning(f"Ticket cache sync failed (non-fatal): {e}")
            cache_stats = {}

        # ── Process new tickets ─────────────────────────────────────────────
        summary = self.process_tickets()
        summary["cache_stats"] = cache_stats

        # ── Wasabi storage stats ────────────────────────────────────────────
        try:
            storage_stats = self.wasabi.get_storage_stats()
            summary["wasabi_storage"] = storage_stats
            logger.info(
                f"Wasabi storage: {storage_stats['object_count']} objects, "
                f"{storage_stats['total_gb']:.2f} GB"
            )
        except Exception as e:
            logger.warning(f"Could not fetch Wasabi storage stats (non-fatal): {e}")
            summary["wasabi_storage"] = {"error": str(e)}

        # ── Zendesk storage stats (from local DB) ───────────────────────────
        try:
            summary["zendesk_storage"] = self.get_zendesk_storage_stats()
        except Exception as e:
            logger.warning(f"Could not compute Zendesk storage stats (non-fatal): {e}")
            summary["zendesk_storage"] = {"error": str(e)}

        # ── Collect all S3 keys from this run ───────────────────────────────
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
                "attachments_deleted": summary.get("attachments_deleted", 0),
                "inlines_uploaded": summary.get("inlines_uploaded", 0),
                "inlines_deleted": summary.get("inlines_deleted", 0),
                "errors": summary["errors"],
                "all_s3_keys": all_s3_keys,
                "wasabi_storage": summary.get("wasabi_storage", {}),
                "zendesk_storage": summary.get("zendesk_storage", {}),
                "cache_stats": summary.get("cache_stats", {}),
                "details": summary.get("details", [])
            }
            
            log_entry = OffloadLog(
                run_date=summary["run_date"],
                tickets_processed=summary["tickets_processed"],
                attachments_uploaded=summary["attachments_uploaded"],
                inlines_uploaded=summary.get("inlines_uploaded", 0),
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

    # ------------------------------------------------------------------
    # RECHECK ALL TICKETS
    # ------------------------------------------------------------------

    def process_all_tickets_recheck(self, progress_callback=None) -> Dict:
        """
        Smart recheck: only scan tickets that have 0 attachments recorded in DB
        (or are completely missing from DB). For each, check Zendesk live and
        process if attachments are still present.

        progress_callback(current, total, ticket_id) is called after each ticket
        so the scheduler can expose live progress to the API.
        """
        summary = {
            "run_date": datetime.utcnow(),
            "tickets_scanned": 0,
            "tickets_total": 0,
            "tickets_with_remaining_attachments": 0,
            "tickets_processed": 0,
            "tickets_genuinely_empty": 0,
            "tickets_404": 0,
            "attachments_uploaded": 0,
            "attachments_deleted": 0,
            "inlines_uploaded": 0,
            "inlines_deleted": 0,
            "errors": [],
            "skipped_reasons": {},   # reason -> count
            "details": []            # only tickets that had something to process
        }

        try:
            logger.info("=" * 60)
            logger.info("Starting SMART RECHECK...")
            logger.info("=" * 60)

            # ── Step 1: collect candidate ticket IDs ─────────────────────
            db = get_db()
            try:
                # Tickets in DB with 0 uploads
                zero_upload_ids = [
                    row[0] for row in
                    db.query(ProcessedTicket.ticket_id).filter(
                        ProcessedTicket.attachments_count == 0
                    ).all()
                ]
                all_db_ids = set(
                    row[0] for row in db.query(ProcessedTicket.ticket_id).all()
                )
                # Use the local cache to find tickets missing from processed_tickets.
                # Fall back to Zendesk API only if the cache is empty.
                cache_count = db.query(ZendeskTicketCache.ticket_id).count()
                if cache_count > 0:
                    all_cached_ids = [
                        row[0] for row in db.query(ZendeskTicketCache.ticket_id).all()
                    ]
                    missing_from_db = [tid for tid in all_cached_ids if tid not in all_db_ids]
                    logger.info(
                        f"Using local cache ({cache_count} tickets) to find missing IDs "
                        f"— {len(missing_from_db)} not yet in processed_tickets."
                    )
                else:
                    # Cache is empty — do the expensive Zendesk API call once and populate it
                    logger.info("Local cache is empty, fetching all tickets from Zendesk to populate it…")
                    self.sync_ticket_cache()
                    all_cached_ids = [
                        row[0] for row in db.query(ZendeskTicketCache.ticket_id).all()
                    ]
                    missing_from_db = [tid for tid in all_cached_ids if tid not in all_db_ids]
            finally:
                db.close()

            candidate_ids = list(set(zero_upload_ids + missing_from_db))
            candidate_ids.sort()

            summary["tickets_total"] = len(candidate_ids)
            logger.info(
                f"Candidates: {len(zero_upload_ids)} with 0 uploads in DB + "
                f"{len(missing_from_db)} missing from DB = {len(candidate_ids)} total to check"
            )

            # ── Step 2: check each candidate against Zendesk ─────────────
            for idx, ticket_id in enumerate(candidate_ids):
                summary["tickets_scanned"] = idx + 1

                if progress_callback:
                    progress_callback(idx + 1, len(candidate_ids), ticket_id)

                db = get_db()
                try:
                    attachments = self.zendesk.get_ticket_attachments(ticket_id)
                    inline_images = self.zendesk.get_inline_images(ticket_id)

                    inline_attachment_ids = {
                        img.get("attachment_id") for img in inline_images
                        if img.get("attachment_id")
                    }
                    remaining_regular = [
                        a for a in attachments if a.get("id") not in inline_attachment_ids
                    ]
                    remaining_count = len(remaining_regular) + len(inline_images)

                    if remaining_count == 0:
                        # Genuinely no attachments (or already offloaded)
                        summary["tickets_genuinely_empty"] += 1
                        # Make sure it's in DB so it won't be rechecked next time
                        existing = db.query(ProcessedTicket).filter_by(ticket_id=ticket_id).first()
                        if not existing:
                            db.add(ProcessedTicket(
                                ticket_id=ticket_id,
                                attachments_count=0,
                                status="processed",
                            ))
                            db.commit()
                        continue

                    # Has attachments — process it
                    summary["tickets_with_remaining_attachments"] += 1
                    logger.info(
                        f"Recheck [{idx+1}/{len(candidate_ids)}] ticket {ticket_id}: "
                        f"{remaining_count} attachment(s) still present — processing..."
                    )

                    result = self.process_ticket(ticket_id)
                    summary["tickets_processed"] += 1
                    summary["attachments_uploaded"] += result.get("attachments_uploaded", 0)
                    summary["attachments_deleted"] += result.get("attachments_deleted", 0)
                    summary["inlines_uploaded"] += result.get("inlines_uploaded", 0)
                    summary["inlines_deleted"] += result.get("inlines_deleted", 0)
                    summary["details"].append(result)

                    s3_keys = [f["s3_key"] for f in result.get("uploaded_files", [])]
                    wasabi_files_json = json.dumps(s3_keys) if s3_keys else None

                    existing = db.query(ProcessedTicket).filter_by(ticket_id=ticket_id).first()
                    if existing:
                        existing.processed_at = datetime.utcnow()
                        existing.attachments_count = result.get("attachments_uploaded", 0)
                        existing.status = "processed"
                        existing.error_message = (
                            "; ".join(result.get("errors", [])[:5])
                            if result.get("errors") else None
                        )
                        if wasabi_files_json:
                            existing.wasabi_files = wasabi_files_json
                    else:
                        db.add(ProcessedTicket(
                            ticket_id=ticket_id,
                            attachments_count=result.get("attachments_uploaded", 0),
                            status="processed",
                            error_message=(
                                "; ".join(result.get("errors", [])[:5])
                                if result.get("errors") else None
                            ),
                            wasabi_files=wasabi_files_json
                        ))
                    db.commit()

                    if result.get("errors"):
                        summary["errors"].extend(
                            [f"Ticket {ticket_id}: {e}" for e in result["errors"]]
                        )

                except Exception as e:
                    db.rollback()
                    status_code = None
                    if hasattr(e, 'response') and e.response is not None:
                        status_code = e.response.status_code
                    if status_code == 404:
                        summary["tickets_404"] += 1
                        summary["skipped_reasons"]["404_deleted"] = (
                            summary["skipped_reasons"].get("404_deleted", 0) + 1
                        )
                    else:
                        error_msg = f"Error rechecking ticket {ticket_id}: {str(e)}"
                        logger.error(error_msg)
                        summary["errors"].append(error_msg)
                finally:
                    db.close()

        except Exception as e:
            error_msg = f"Critical recheck error: {str(e)}"
            logger.error(error_msg, exc_info=True)
            summary["errors"].append(error_msg)

        logger.info(
            f"Recheck complete. Checked: {summary['tickets_scanned']}/{summary['tickets_total']}, "
            f"Found attachments: {summary['tickets_with_remaining_attachments']}, "
            f"Processed: {summary['tickets_processed']}, "
            f"Uploaded: {summary['attachments_uploaded']}, "
            f"Genuinely empty: {summary['tickets_genuinely_empty']}, "
            f"404/deleted: {summary['tickets_404']}, "
            f"Errors: {len(summary['errors'])}"
        )
        return summary

    def run_recheck_all_offload(self, progress_callback=None) -> Dict:
        """Run full recheck mode and persist a log entry."""
        logger.info(f"Starting full recheck offload at {datetime.utcnow()}")

        summary = self.process_all_tickets_recheck(progress_callback=progress_callback)

        all_s3_keys = []
        for detail in summary.get("details", []):
            if isinstance(detail, dict):
                for f in detail.get("uploaded_files", []):
                    if isinstance(f, dict) and "s3_key" in f:
                        all_s3_keys.append({
                            "ticket_id": detail.get("ticket_id"),
                            "s3_key": f["s3_key"],
                            "original_filename": f.get("original", "")
                        })

        summary["all_s3_keys"] = all_s3_keys

        db = get_db()
        try:
            summary_for_storage = {
                "run_mode": "recheck_all",
                "run_date": summary["run_date"].isoformat(),
                "tickets_total": summary.get("tickets_total", 0),
                "tickets_scanned": summary.get("tickets_scanned", 0),
                "tickets_with_remaining_attachments": summary.get("tickets_with_remaining_attachments", 0),
                "tickets_processed": summary.get("tickets_processed", 0),
                "tickets_genuinely_empty": summary.get("tickets_genuinely_empty", 0),
                "tickets_404": summary.get("tickets_404", 0),
                "attachments_uploaded": summary.get("attachments_uploaded", 0),
                "attachments_deleted": summary.get("attachments_deleted", 0),
                "inlines_uploaded": summary.get("inlines_uploaded", 0),
                "inlines_deleted": summary.get("inlines_deleted", 0),
                "errors": summary.get("errors", []),
                "skipped_reasons": summary.get("skipped_reasons", {}),
                "all_s3_keys": all_s3_keys,
                "details": summary.get("details", [])
            }

            log_entry = OffloadLog(
                run_date=summary["run_date"],
                tickets_processed=summary.get("tickets_processed", 0),
                attachments_uploaded=summary.get("attachments_uploaded", 0),
                inlines_uploaded=summary.get("inlines_uploaded", 0),
                errors_count=len(summary.get("errors", [])),
                status="completed" if not summary.get("errors") else "completed_with_errors",
                report_sent=False,
                details=json.dumps(summary_for_storage)
            )
            db.add(log_entry)
            db.commit()
            summary["log_id"] = log_entry.id
        finally:
            db.close()

        return summary



