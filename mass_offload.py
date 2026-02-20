#!/usr/bin/env python3
"""
One-time mass offload for a specific list of ticket IDs from a CSV.
Usage: python3 mass_offload.py <csv_file>
Sends a Telegram report when done.
"""
import sys
import csv
import logging
import os
from datetime import datetime

# ── Bootstrap path ─────────────────────────────────────────────────────────
sys.path.insert(0, '/opt/z2w')
os.chdir('/opt/z2w')

from logger_config import setup_logging
setup_logging()
logger = logging.getLogger('zendesk_offloader')

from offloader import AttachmentOffloader
from database import get_db, ProcessedTicket
from telegram_reporter import TelegramReporter

def get_already_offloaded(ticket_ids):
    """Return set of ticket IDs that already have uploads in DB."""
    db = get_db()
    try:
        rows = db.query(ProcessedTicket.ticket_id, ProcessedTicket.attachments_count)\
                 .filter(ProcessedTicket.ticket_id.in_(ticket_ids))\
                 .all()
        return {r.ticket_id for r in rows if r.attachments_count and r.attachments_count > 0}
    finally:
        db.close()

def run_mass_offload(csv_path: str):
    start_time = datetime.now()
    print(f"[{start_time:%Y-%m-%d %H:%M:%S}] Mass offload started from: {csv_path}")

    # ── Read unique ticket IDs from CSV or plain text file ───────────────────
    ticket_ids = set()
    with open(csv_path, newline='', encoding='utf-8') as f:
        first_line = f.readline().strip()
        f.seek(0)
        # Detect format: plain text (one ID per line) vs CSV
        if first_line.isdigit():
            for line in f:
                line = line.strip()
                if line.isdigit():
                    ticket_ids.add(int(line))
        else:
            reader = csv.DictReader(f)
            for row in reader:
                tid_raw = row.get('ticket_id', '').strip()
                if tid_raw:
                    try:
                        ticket_ids.add(int(float(tid_raw)))
                    except ValueError:
                        pass

    ticket_ids = sorted(ticket_ids)
    total_tickets = len(ticket_ids)
    print(f"Found {total_tickets} unique ticket IDs in CSV.")

    # ── Check which are already done ────────────────────────────────────────
    already_done = get_already_offloaded(ticket_ids)
    to_process = [tid for tid in ticket_ids if tid not in already_done]
    skipped_already = len(already_done)

    print(f"Already offloaded: {skipped_already}  |  To process: {len(to_process)}")

    # ── Initialise offloader ─────────────────────────────────────────────────
    offloader = AttachmentOffloader()

    # ── Per-ticket stats ─────────────────────────────────────────────────────
    results = {
        "success": [],        # (ticket_id, files_uploaded, size_bytes)
        "skipped_no_att": [], # ticket had 0 attachments to move
        "failed": [],         # (ticket_id, error)
    }
    total_files  = 0
    total_bytes  = 0

    for idx, ticket_id in enumerate(to_process, 1):
        print(f"[{idx}/{len(to_process)}] Processing ticket {ticket_id}…")
        try:
            res = offloader.process_ticket(ticket_id)
            uploaded = res.get("attachments_uploaded", 0)
            size     = res.get("total_size_bytes", 0)
            errors   = res.get("errors", [])

            if errors and uploaded == 0:
                results["failed"].append((ticket_id, "; ".join(str(e) for e in errors[:2])))
                print(f"  ✗ Failed: {errors[0]}")
            elif uploaded == 0:
                results["skipped_no_att"].append(ticket_id)
                print(f"  – No attachments to offload")
            else:
                results["success"].append((ticket_id, uploaded, size))
                total_files += uploaded
                total_bytes += size
                print(f"  ✓ Uploaded {uploaded} file(s) ({size/1024/1024:.1f} MB)")

        except Exception as e:
            results["failed"].append((ticket_id, str(e)))
            logger.error(f"Mass offload: ticket {ticket_id} raised exception: {e}", exc_info=True)
            print(f"  ✗ Exception: {e}")

    end_time = datetime.now()
    elapsed  = end_time - start_time
    elapsed_str = f"{int(elapsed.total_seconds()//60)}m {int(elapsed.total_seconds()%60)}s"

    # ── Build Telegram report ────────────────────────────────────────────────
    success_count       = len(results["success"])
    skipped_no_att      = len(results["skipped_no_att"])
    failed_count        = len(results["failed"])
    total_mb            = total_bytes / 1024 / 1024

    lines = [
        f"📦 <b>Mass Offload Complete</b>",
        f"",
        f"📋 CSV tickets : {total_tickets}",
        f"✅ Already done: {skipped_already}",
        f"🔄 Processed   : {len(to_process)}",
        f"",
        f"✅ Succeeded   : {success_count}",
        f"📁 Files moved : {total_files}",
        f"💾 Data freed  : {total_mb:.1f} MB",
        f"➖ No attachments: {skipped_no_att}",
        f"❌ Failed      : {failed_count}",
        f"",
        f"⏱ Duration    : {elapsed_str}",
    ]

    if results["failed"]:
        lines.append(f"")
        lines.append(f"<b>Failed tickets:</b>")
        for tid, err in results["failed"][:20]:
            lines.append(f"  • #{tid}: {err[:80]}")
        if failed_count > 20:
            lines.append(f"  … and {failed_count - 20} more")

    message = "\n".join(lines)

    try:
        reporter = TelegramReporter()
        reporter.send_message(message)
        print("\nTelegram report sent.")
    except Exception as e:
        print(f"\nTelegram send failed: {e}")
        logger.error(f"Mass offload Telegram report failed: {e}")

    # ── Console summary ──────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print(f"MASS OFFLOAD SUMMARY")
    print(f"{'='*60}")
    print(f"Total tickets in CSV : {total_tickets}")
    print(f"Already offloaded    : {skipped_already}")
    print(f"Processed            : {len(to_process)}")
    print(f"  Succeeded          : {success_count}  ({total_files} files, {total_mb:.1f} MB)")
    print(f"  No attachments     : {skipped_no_att}")
    print(f"  Failed             : {failed_count}")
    print(f"Duration             : {elapsed_str}")
    if results["failed"]:
        print(f"\nFailed tickets:")
        for tid, err in results["failed"]:
            print(f"  #{tid}: {err}")
    print(f"{'='*60}")


if __name__ == "__main__":
    csv_file = sys.argv[1] if len(sys.argv) > 1 else \
        "/opt/z2w/logs/temp/go4rex-largest-attachments-2026-02-18T22-49-37-330Z.csv"
    run_mass_offload(csv_file)
