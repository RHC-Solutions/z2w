#!/usr/bin/env python3
"""
One-time bulk re-offload script.
Finds all tickets that still have files in Zendesk (from zendesk_storage_snapshot)
and re-processes them — uploading attachments + inline images to Wasabi and
replacing them in Zendesk.

Usage:  .venv/bin/python3 bulk_reoffload.py
"""
import json
import time
import sys
from datetime import datetime
from database import get_db, ProcessedTicket, ZendeskStorageSnapshot, upsert_processed_ticket
from offloader import AttachmentOffloader
from logger_config import setup_logging

logger = setup_logging()


def main():
    db = get_db()
    try:
        # Find all tickets that still have files in Zendesk
        rows = db.query(ZendeskStorageSnapshot).filter(
            ZendeskStorageSnapshot.total_size > 0
        ).order_by(ZendeskStorageSnapshot.total_size.desc()).all()

        all_ticket_ids = [r.ticket_id for r in rows]
        total_bytes = sum(r.total_size for r in rows)

        # Skip tickets already successfully processed (resumable)
        already_done = set()
        existing = db.query(ProcessedTicket).filter(
            ProcessedTicket.ticket_id.in_(all_ticket_ids),
            ProcessedTicket.status == 'processed',
            ProcessedTicket.attachments_count > 0,
        ).all()
        already_done = {p.ticket_id for p in existing}

        ticket_ids = [tid for tid in all_ticket_ids if tid not in already_done]

        print(f"\n{'='*60}")
        print(f"  BULK RE-OFFLOAD (resumable)")
        print(f"  Tickets with files in Zendesk: {len(all_ticket_ids)}")
        print(f"  Already done (skipping):       {len(already_done)}")
        print(f"  Remaining to process:          {len(ticket_ids)}")
        print(f"  Total size (all):              {total_bytes / 1073741824:.2f} GB")
        print(f"{'='*60}\n")

        if not ticket_ids:
            print("Nothing to offload — all clean!")
            return

        # Delete existing error/incomplete records for remaining tickets
        deleted = db.query(ProcessedTicket).filter(
            ProcessedTicket.ticket_id.in_(ticket_ids)
        ).delete(synchronize_session='fetch')
        db.commit()
        if deleted:
            print(f"Cleared {deleted} error/incomplete records.\n")
    finally:
        db.close()

    # Now process each ticket
    offloader = AttachmentOffloader()
    total = len(ticket_ids)
    success = 0
    errors = 0
    total_uploaded = 0
    total_size_uploaded = 0

    for i, ticket_id in enumerate(ticket_ids, 1):
        print(f"[{i}/{total}] Ticket #{ticket_id} …", end=" ", flush=True)
        db = get_db()
        try:
            result = offloader.process_ticket(ticket_id)
            uploaded = result.get("attachments_uploaded", 0)
            size = result.get("total_size_bytes", 0)
            errs = result.get("errors", [])

            s3_keys = [f["s3_key"] for f in result.get("uploaded_files", [])]
            wasabi_files_json = json.dumps(s3_keys) if s3_keys else None

            upsert_processed_ticket(
                db,
                ticket_id=ticket_id,
                attachments_count=uploaded,
                status="processed",
                error_message=None,
                wasabi_files=wasabi_files_json,
                wasabi_files_size=size,
            )

            total_uploaded += uploaded
            total_size_uploaded += size

            if errs:
                errors += 1
                print(f"⚠  {uploaded} files ({size/1048576:.1f} MB) — {len(errs)} error(s): {errs[0][:80]}")
            elif uploaded > 0:
                success += 1
                print(f"✓  {uploaded} files ({size/1048576:.1f} MB)")
            else:
                success += 1
                print(f"–  no files to offload")

        except Exception as e:
            errors += 1
            print(f"✗  {e}")
            try:
                upsert_processed_ticket(
                    db,
                    ticket_id=ticket_id,
                    attachments_count=0,
                    status="error",
                    error_message=str(e),
                )
            except Exception:
                db.rollback()
        finally:
            db.close()

        # Small delay to avoid Zendesk rate limits
        time.sleep(0.3)

    print(f"\n{'='*60}")
    print(f"  DONE")
    print(f"  Tickets: {total} total, {success} ok, {errors} errors")
    print(f"  Uploaded: {total_uploaded} files, {total_size_uploaded/1048576:.1f} MB")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
