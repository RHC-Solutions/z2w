#!/usr/bin/env python3
"""
Scan remaining tickets (beyond the 1,000 search-API limit) for un-redacted
inline images and offload them to Wasabi + redact from Zendesk.

Uses the local ZendeskTicketCache instead of the search API, skipping
tickets already processed by bulk_inline_offload.py.

Usage:
    python3 bulk_inline_remaining.py              # Live run
    python3 bulk_inline_remaining.py --dry-run    # Scan only
    python3 bulk_inline_remaining.py --limit 100  # Process at most 100 tickets
"""
import sys
import os
import re
import time
import json
import logging
import argparse
from datetime import datetime

sys.path.insert(0, '/opt/z2w')
os.chdir('/opt/z2w')

from logger_config import setup_logging
setup_logging()
logger = logging.getLogger('zendesk_offloader')

from zendesk_client import ZendeskClient
from wasabi_client import WasabiClient
from database import (
    get_db, ProcessedTicket, ZendeskTicketCache,
    ZendeskStorageSnapshot, Setting, upsert_processed_ticket,
)
from sqlalchemy import func

LOG_FILE = '/tmp/bulk_inline_remaining.log'
STATE_FILE = '/tmp/bulk_inline_remaining_scanned.json'


def find_inline_image_urls(html_body: str) -> list:
    """Extract Zendesk-hosted inline image URLs from comment HTML."""
    if not html_body:
        return []
    pattern = r'<img[^>]+src=["\']([^"\']*attachments[^"\']*)["\'][^>]*>'
    return list(re.finditer(pattern, html_body, re.IGNORECASE))


def scan_ticket_inlines_only(zd: ZendeskClient, ticket_id: int) -> list:
    """
    Scan a single ticket for inline images not tracked in the attachments array.
    Returns a list of inline image dicts.
    """
    inlines = []

    for attempt in range(3):
        try:
            resp = zd.session.get(
                f"{zd.base_url}/tickets/{ticket_id}/comments.json",
                timeout=30,
            )
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 15))
                time.sleep(wait)
                resp = zd.session.get(
                    f"{zd.base_url}/tickets/{ticket_id}/comments.json", timeout=30
                )
            if not resp.ok:
                return inlines
            break
        except (ConnectionError, Exception) as e:
            if attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            return inlines

    comments = resp.json().get("comments", [])

    for comment in comments:
        comment_id = comment.get("id")
        html_body = comment.get("html_body", "") or ""

        # Build set of attachment tokens to skip duplicates
        att_tokens = set()
        for att in comment.get("attachments", []):
            url = att.get("content_url", "")
            token_m = re.search(r'/attachments/token/([^/?]+)', url)
            if token_m:
                att_tokens.add(token_m.group(1))

        matches = find_inline_image_urls(html_body)
        for match in matches:
            img_url = match.group(1)
            original_html = match.group(0)

            # Skip if tracked via attachments array
            token_m = re.search(r'/attachments/token/([^/?]+)', img_url)
            if token_m and token_m.group(1) in att_tokens:
                continue

            # Extract filename
            filename = "inline_image.png"
            name_m = re.search(r'[?&]name=([^&]+)', img_url)
            if name_m:
                filename = name_m.group(1)
            else:
                fn_m = re.search(
                    r'/([^/?]+\.(?:jpg|jpeg|png|gif|bmp|webp|svg))',
                    img_url, re.IGNORECASE,
                )
                if fn_m:
                    filename = fn_m.group(1)

            ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
            content_type = {
                'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
                'png': 'image/png', 'gif': 'image/gif',
                'webp': 'image/webp', 'svg': 'image/svg+xml',
            }.get(ext, 'image/png')

            inlines.append({
                "ticket_id": ticket_id,
                "comment_id": comment_id,
                "img_url": img_url,
                "original_html": original_html,
                "html_body": html_body,
                "file_name": filename,
                "content_type": content_type,
            })

    return inlines


def main():
    parser = argparse.ArgumentParser(
        description="Scan remaining tickets for un-redacted inline images"
    )
    parser.add_argument("--dry-run", action="store_true", help="Scan only")
    parser.add_argument("--limit", type=int, default=0, help="Max tickets to process")
    args = parser.parse_args()

    start = datetime.utcnow()
    mode = "DRY RUN" if args.dry_run else "LIVE"
    print(f"[{start:%Y-%m-%d %H:%M:%S} UTC] Remaining-ticket inline scan ({mode})")
    print("=" * 70)

    # Set up log file
    fh = logging.FileHandler(LOG_FILE, mode='a')
    fh.setFormatter(logging.Formatter('%(asctime)s %(message)s'))
    log = logging.getLogger('bulk_remaining')
    log.addHandler(fh)
    log.setLevel(logging.INFO)
    log.info(f"=== Run started ({mode}) ===")

    zd = ZendeskClient()
    wasabi = WasabiClient()

    # ── Get all ticket IDs from cache ────────────────────────────────────
    db = get_db()
    all_tids = sorted(
        r[0] for r in db.query(ZendeskTicketCache.ticket_id).all()
    )
    db.close()
    print(f"   Total tickets in cache: {len(all_tids)}")

    # ── Load IDs already processed by the first bulk script ──────────────
    already_done = set()
    if os.path.exists('/tmp/bulk_inline_offload.log'):
        with open('/tmp/bulk_inline_offload.log') as f:
            content = f.read()
        already_done = set(
            int(x) for x in re.findall(r'ticket #(\d+)', content, re.IGNORECASE)
        )
    print(f"   Already done by bulk script: {len(already_done)}")

    remaining = [t for t in all_tids if t not in already_done]
    print(f"   Remaining to scan: {len(remaining)}")

    # ── Load resume state (tickets already scanned with 0 results) ───────
    scanned_zero = set()
    if os.path.exists(STATE_FILE):
        try:
            with open(STATE_FILE) as f:
                scanned_zero = set(json.load(f))
            print(f"   Resuming — {len(scanned_zero)} tickets already scanned (0 inlines)")
        except Exception:
            pass

    to_scan = [t for t in remaining if t not in scanned_zero]
    print(f"   Tickets to scan this run: {len(to_scan)}")

    # ── Phase 1: Scan for inline images ──────────────────────────────────
    print(f"\n🔍 Phase 1: Scanning {len(to_scan)} tickets for inline images…")
    actionable = []  # list of (ticket_id, [inline_dicts])
    scanned = 0
    total_found = 0

    for tid in to_scan:
        inlines = scan_ticket_inlines_only(zd, tid)
        if inlines:
            # Verify images are still live (not already redacted/404)
            live = []
            for inl in inlines:
                for att in range(3):
                    try:
                        url = inl["img_url"]
                        if url.startswith('/'):
                            url = f"https://{zd.subdomain}.zendesk.com{url}"
                        hr = zd.session.get(url, timeout=10, stream=True)
                        size = int(hr.headers.get('Content-Length', 0))
                        hr.close()
                        if size > 100:
                            inl["size"] = size
                            live.append(inl)
                        break
                    except Exception:
                        if att < 2:
                            time.sleep(3)
                            continue
                        break
            if live:
                actionable.append((tid, live))
                total_found += len(live)
        else:
            scanned_zero.add(tid)

        scanned += 1
        if scanned % 500 == 0:
            print(f"   …scanned {scanned}/{len(to_scan)} — "
                  f"{total_found} live inlines in {len(actionable)} tickets")
            # Save resume state periodically
            with open(STATE_FILE, 'w') as f:
                json.dump(sorted(scanned_zero), f)
        time.sleep(0.15)

    # Final save of resume state
    with open(STATE_FILE, 'w') as f:
        json.dump(sorted(scanned_zero), f)

    total_bytes = sum(
        sum(inl.get("size", 0) for inl in inlines)
        for _, inlines in actionable
    )
    print(f"\n   Scan complete: {scanned} tickets scanned")
    print(f"   Tickets with live inlines: {len(actionable)}")
    print(f"   Total inline images: {total_found}")
    print(f"   Total size: {total_bytes / 1048576:.1f} MB")
    log.info(f"Scan: {scanned} scanned, {len(actionable)} actionable, "
             f"{total_found} inlines, {total_bytes/1048576:.1f} MB")

    if args.dry_run:
        print("\n🏁 DRY RUN — no changes made.")
        if actionable:
            print(f"\n{'#TID':<8} {'Inl':>4} {'Size':>10}")
            print("-" * 25)
            actionable.sort(key=lambda x: sum(i.get("size", 0) for i in x[1]), reverse=True)
            for tid, inlines in actionable[:30]:
                sz = sum(i.get("size", 0) for i in inlines)
                print(f"#{tid:<7} {len(inlines):>4} {sz/1024:>9.1f}K")
        return

    if not actionable:
        print("\n✅ No un-redacted inline images found!")
        return

    # ── Phase 2: Offload + Redact ────────────────────────────────────────
    if args.limit > 0:
        actionable.sort(
            key=lambda x: sum(i.get("size", 0) for i in x[1]), reverse=True
        )
        actionable = actionable[:args.limit]
        print(f"\n   (Limited to {args.limit} tickets)")

    print(f"\n🚀 Phase 2: Offloading & redacting {len(actionable)} tickets…")

    stats = {
        "tickets_processed": 0,
        "inl_uploaded": 0,
        "inl_redacted": 0,
        "bytes_freed": 0,
        "errors": [],
    }

    for idx, (tid, inlines) in enumerate(actionable, 1):
        print(f"\n[{idx}/{len(actionable)}] Ticket #{tid}: {len(inlines)} inlines")
        log.info(f"Processing ticket #{tid}: {len(inlines)} inlines")

        ticket_uploaded = 0
        ticket_bytes = 0
        s3_keys = []

        for inl in inlines:
            comment_id = inl["comment_id"]
            img_url = inl["img_url"]
            original_html = inl["original_html"]
            filename = inl["file_name"]
            content_type = inl["content_type"]

            try:
                # Download (with retry)
                url = img_url
                if url.startswith('/'):
                    url = f"https://{zd.subdomain}.zendesk.com{url}"
                dl = None
                for att in range(3):
                    try:
                        dl = zd.session.get(url, timeout=30)
                        break
                    except Exception:
                        if att < 2:
                            time.sleep(5 * (att + 1))
                            continue
                        raise
                if dl is None or not dl.ok:
                    stats["errors"].append(
                        f"#{tid}: download failed for {filename} (HTTP {dl.status_code if dl else 'N/A'})"
                    )
                    continue

                image_data = dl.content
                if not image_data or len(image_data) < 100:
                    continue

                # Upload to Wasabi
                s3_key = wasabi.upload_attachment(
                    ticket_id=tid,
                    attachment_data=image_data,
                    original_filename=filename,
                    content_type=content_type,
                )
                if not s3_key:
                    stats["errors"].append(f"#{tid}: Wasabi upload failed for {filename}")
                    continue

                image_size = len(image_data)
                ticket_uploaded += 1
                ticket_bytes += image_size
                s3_keys.append(s3_key)
                stats["inl_uploaded"] += 1

                # Get Wasabi URL
                wasabi_url = wasabi.get_file_url(s3_key, expires_in=31536000)

                # Redact via Agent Workspace API
                if wasabi_url:
                    success = zd.redact_inline_image_agent_workspace(
                        ticket_id=tid,
                        comment_id=comment_id,
                        wasabi_url=wasabi_url,
                        filename=filename,
                        original_html=original_html,
                    )
                    if success:
                        stats["inl_redacted"] += 1
                        stats["bytes_freed"] += image_size
                        print(f"   ✓ {filename} ({image_size/1024:.0f} KB)")
                    else:
                        print(f"   ⚠ {filename} uploaded, redact failed")
                        stats["errors"].append(
                            f"#{tid}: AW redact failed for {filename}"
                        )

                time.sleep(0.5)

            except Exception as e:
                stats["errors"].append(f"#{tid}: {filename}: {e}")
                print(f"   ✗ {filename}: {e}")

        # Update DB
        if ticket_uploaded > 0:
            db = get_db()
            try:
                existing = db.query(ProcessedTicket).filter_by(ticket_id=tid).first()
                old_count = existing.attachments_count if existing else 0
                old_size = existing.wasabi_files_size if existing else 0
                old_keys = []
                if existing and existing.wasabi_files:
                    try:
                        old_keys = json.loads(existing.wasabi_files)
                    except (json.JSONDecodeError, TypeError):
                        pass
                merged_keys = old_keys + s3_keys

                upsert_processed_ticket(
                    db,
                    ticket_id=tid,
                    attachments_count=(old_count or 0) + ticket_uploaded,
                    status="processed",
                    error_message=None,
                    wasabi_files=json.dumps(merged_keys) if merged_keys else None,
                    wasabi_files_size=(old_size or 0) + ticket_bytes,
                )
            finally:
                db.close()

            stats["tickets_processed"] += 1

    # ── Summary ──────────────────────────────────────────────────────────
    elapsed = (datetime.utcnow() - start).total_seconds()
    print("\n" + "=" * 70)
    print("🏁 REMAINING-TICKET INLINE SCAN COMPLETE")
    print("=" * 70)
    print(f"   Duration: {elapsed/60:.1f} min")
    print(f"   Tickets scanned: {scanned}")
    print(f"   Tickets processed: {stats['tickets_processed']}")
    print(f"   Inline images: {stats['inl_uploaded']} uploaded, "
          f"{stats['inl_redacted']} redacted")
    print(f"   Storage freed: {stats['bytes_freed']/1048576:.1f} MB")
    print(f"   Errors: {len(stats['errors'])}")
    if stats["errors"]:
        print("\n   First 10 errors:")
        for err in stats["errors"][:10]:
            print(f"     • {err}")

    log.info(f"Complete: {stats['tickets_processed']} tickets, "
             f"{stats['inl_uploaded']} uploaded, {stats['inl_redacted']} redacted, "
             f"{stats['bytes_freed']/1048576:.1f} MB freed, "
             f"{len(stats['errors'])} errors")

    # Telegram report
    try:
        from telegram_reporter import TelegramReporter
        tr = TelegramReporter()
        msg = (
            f"🗃 *Remaining Inline Scan Complete*\n\n"
            f"⏱ Duration: {elapsed/60:.1f} min\n"
            f"🔍 Scanned: {scanned} tickets\n"
            f"🖼 Inlines: {stats['inl_uploaded']} ↗ {stats['inl_redacted']} 🗑\n"
            f"💾 Freed: {stats['bytes_freed']/1048576:.1f} MB\n"
            f"❌ Errors: {len(stats['errors'])}"
        )
        tr.send_message(msg)
    except Exception as e:
        print(f"   (Telegram report failed: {e})")


if __name__ == "__main__":
    main()
