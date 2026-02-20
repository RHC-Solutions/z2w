#!/usr/bin/env python3
"""
Bulk offload & redact inline images + remaining file attachments from Zendesk.

This script:
1. Scans ALL tickets (via Zendesk search API) for tickets with attachments
2. For each ticket with real files or inline images still on Zendesk:
   a. Downloads the file/image
   b. Uploads it to Wasabi
   c. Redacts it from Zendesk (via Agent Workspace API for inlines,
      or attachment redact API for regular files)
3. Updates the ProcessedTicket DB record
4. Sends a Telegram summary when done

Usage:
    python3 bulk_inline_offload.py              # Process all tickets
    python3 bulk_inline_offload.py --dry-run    # Just scan and report, no changes
    python3 bulk_inline_offload.py --limit 50   # Process at most 50 tickets
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


# ── Helpers ──────────────────────────────────────────────────────────────────

def find_inline_image_urls(html_body: str) -> list:
    """Extract Zendesk-hosted inline image URLs from comment HTML."""
    if not html_body:
        return []
    pattern = r'<img[^>]+src=["\']([^"\']*attachments[^"\']*)["\'][^>]*>'
    return list(re.finditer(pattern, html_body, re.IGNORECASE))


def scan_ticket(zd: ZendeskClient, ticket_id: int) -> dict:
    """
    Scan a single ticket and return info about what still needs offloading.

    Returns dict with:
      - regular_attachments: list of dicts (non-redacted file attachments)
      - inline_images: list of dicts (inline images in HTML, no attachment entry)
      - total_bytes: sum of known sizes
    """
    result = {
        "ticket_id": ticket_id,
        "regular_attachments": [],
        "inline_images": [],
        "total_bytes": 0,
    }

    resp = zd.session.get(
        f"{zd.base_url}/tickets/{ticket_id}/comments.json",
        timeout=30,
    )
    if resp.status_code == 429:
        time.sleep(int(resp.headers.get("Retry-After", 15)))
        resp = zd.session.get(
            f"{zd.base_url}/tickets/{ticket_id}/comments.json", timeout=30
        )
    if not resp.ok:
        return result

    comments = resp.json().get("comments", [])

    for comment in comments:
        comment_id = comment.get("id")
        html_body = comment.get("html_body", "") or ""

        # ── Regular attachments ──────────────────────────────────────────
        for att in comment.get("attachments", []):
            fn = att.get("file_name", "")
            if fn.lower() == "redacted.txt":
                continue  # already redacted
            size = att.get("size", 0)
            result["regular_attachments"].append({
                "attachment_id": att.get("id"),
                "comment_id": comment_id,
                "content_url": att.get("content_url"),
                "file_name": fn,
                "content_type": att.get("content_type", "application/octet-stream"),
                "size": size,
                "inline": att.get("inline", False),
            })
            result["total_bytes"] += size

        # ── Inline images in HTML (token-URL, not in attachments array) ──
        # Build a set of attachment content_urls to avoid double-counting
        att_urls = set()
        for att in comment.get("attachments", []):
            url = att.get("content_url", "")
            token_m = re.search(r'/attachments/token/([^/?]+)', url)
            if token_m:
                att_urls.add(token_m.group(1))

        matches = find_inline_image_urls(html_body)
        for match in matches:
            img_url = match.group(1)
            original_html = match.group(0)

            # Skip if this inline image is already tracked via attachments array
            token_m = re.search(r'/attachments/token/([^/?]+)', img_url)
            if token_m and token_m.group(1) in att_urls:
                continue  # handled as regular attachment above

            # Extract filename from ?name= param or path
            filename = "inline_image.png"
            name_m = re.search(r'[?&]name=([^&]+)', img_url)
            if name_m:
                filename = name_m.group(1)
            else:
                fn_m = re.search(
                    r'/([^/?]+\.(?:jpg|jpeg|png|gif|bmp|webp|svg))',
                    img_url, re.IGNORECASE
                )
                if fn_m:
                    filename = fn_m.group(1)

            # Guess content type
            ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
            content_type = {
                'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
                'png': 'image/png', 'gif': 'image/gif',
                'webp': 'image/webp', 'svg': 'image/svg+xml',
            }.get(ext, 'image/png')

            result["inline_images"].append({
                "comment_id": comment_id,
                "img_url": img_url,
                "original_html": original_html,
                "html_body": html_body,
                "file_name": filename,
                "content_type": content_type,
            })

    return result


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Bulk offload inline images & attachments")
    parser.add_argument("--dry-run", action="store_true", help="Scan only, don't offload/redact")
    parser.add_argument("--limit", type=int, default=0, help="Max tickets to process (0 = all)")
    args = parser.parse_args()

    start = datetime.utcnow()
    print(f"[{start:%Y-%m-%d %H:%M:%S} UTC] Bulk inline offload {'(DRY RUN)' if args.dry_run else 'LIVE'}")
    print("=" * 60)

    zd = ZendeskClient()
    wasabi = WasabiClient()

    # ── Phase 1: Discover tickets with attachments via Zendesk search ────
    print("\n📡 Phase 1: Discovering tickets with attachments…")
    ticket_ids = []
    page = 1
    while True:
        r = zd.session.get(f"{zd.base_url}/search.json", params={
            "query": "type:ticket has_attachment:true",
            "sort_by": "created_at",
            "sort_order": "asc",
            "page": page,
            "per_page": 100,
        })
        if r.status_code == 429:
            time.sleep(int(r.headers.get("Retry-After", 15)))
            continue
        if not r.ok or r.status_code == 422:
            break
        data = r.json()
        results = data.get("results", [])
        if not results:
            break
        for t in results:
            ticket_ids.append(t["id"])
        if not data.get("next_page"):
            break
        page += 1
        time.sleep(0.3)

    print(f"   Found {len(ticket_ids)} tickets flagged with attachments")

    # ── Phase 2: Scan each ticket for actionable items ───────────────────
    print("\n🔍 Phase 2: Scanning tickets for real files & inline images…")
    actionable = []  # (ticket_id, scan_result)
    scanned = 0
    for tid in ticket_ids:
        scan = scan_ticket(zd, tid)
        has_work = len(scan["regular_attachments"]) > 0 or len(scan["inline_images"]) > 0
        if has_work:
            actionable.append((tid, scan))
        scanned += 1
        if scanned % 100 == 0:
            print(f"   …scanned {scanned}/{len(ticket_ids)} — {len(actionable)} actionable so far")
        time.sleep(0.15)

    total_att = sum(len(s["regular_attachments"]) for _, s in actionable)
    total_inl = sum(len(s["inline_images"]) for _, s in actionable)
    total_bytes = sum(s["total_bytes"] for _, s in actionable)
    print(f"\n   Actionable tickets: {len(actionable)}")
    print(f"   Regular attachments: {total_att}")
    print(f"   Inline images: {total_inl}")
    print(f"   Known file size: {total_bytes / 1048576:.1f} MB")

    if args.dry_run:
        print("\n🏁 DRY RUN — no changes made.")
        # Print top tickets
        actionable.sort(key=lambda x: x[1]["total_bytes"], reverse=True)
        print(f"\n{'#TID':<8} {'Att':>4} {'Inl':>4} {'Size':>10}")
        print("-" * 30)
        for tid, scan in actionable[:30]:
            na = len(scan["regular_attachments"])
            ni = len(scan["inline_images"])
            print(f"#{tid:<7} {na:>4} {ni:>4} {scan['total_bytes']/1048576:>9.1f}M")
        return

    if not actionable:
        print("\n✅ Nothing to offload — all clean!")
        return

    # ── Phase 3: Offload + Redact ────────────────────────────────────────
    if args.limit > 0:
        # Prioritise by size (largest first)
        actionable.sort(key=lambda x: x[1]["total_bytes"], reverse=True)
        actionable = actionable[:args.limit]
        print(f"\n   (Limited to {args.limit} tickets)")

    print(f"\n🚀 Phase 3: Offloading & redacting {len(actionable)} tickets…")

    stats = {
        "tickets_processed": 0,
        "att_uploaded": 0,
        "att_redacted": 0,
        "inl_uploaded": 0,
        "inl_redacted": 0,
        "bytes_freed": 0,
        "errors": [],
    }

    for idx, (tid, scan) in enumerate(actionable, 1):
        print(f"\n[{idx}/{len(actionable)}] Ticket #{tid}: "
              f"{len(scan['regular_attachments'])} att, {len(scan['inline_images'])} inl")

        ticket_uploaded = 0
        ticket_bytes = 0
        s3_keys = []

        # ── 3a: Regular attachments ──────────────────────────────────────
        for att in scan["regular_attachments"]:
            att_id = att["attachment_id"]
            comment_id = att["comment_id"]
            filename = att["file_name"]
            content_type = att["content_type"]
            content_url = att["content_url"]
            size = att["size"]

            try:
                # Download
                data = zd.download_attachment(content_url)
                if not data:
                    stats["errors"].append(f"#{tid}: download failed for {filename}")
                    continue

                # Upload to Wasabi
                s3_key = wasabi.upload_attachment(
                    ticket_id=tid,
                    attachment_data=data,
                    original_filename=filename,
                    content_type=content_type,
                )
                if not s3_key:
                    stats["errors"].append(f"#{tid}: Wasabi upload failed for {filename}")
                    continue

                file_size = len(data)
                ticket_uploaded += 1
                ticket_bytes += file_size
                s3_keys.append(s3_key)
                stats["att_uploaded"] += 1

                # Get Wasabi URL
                wasabi_url = wasabi.get_file_url(s3_key, expires_in=31536000)

                # Redact from Zendesk
                if wasabi_url and att_id and comment_id:
                    success = zd.replace_attachment_in_comment(
                        ticket_id=tid,
                        comment_id=comment_id,
                        attachment_id=att_id,
                        wasabi_url=wasabi_url,
                        filename=filename,
                    )
                    if success:
                        stats["att_redacted"] += 1
                        stats["bytes_freed"] += file_size
                        print(f"   ✓ {filename} ({file_size/1024:.0f} KB) → Wasabi + redacted")
                    else:
                        print(f"   ⚠ {filename} uploaded but redaction failed")
                        stats["errors"].append(f"#{tid}: redact failed for {filename}")

                time.sleep(0.3)  # rate-limit courtesy

            except Exception as e:
                stats["errors"].append(f"#{tid}: {filename}: {e}")
                print(f"   ✗ {filename}: {e}")

        # ── 3b: Inline images (token-URL, no attachment_id) ──────────────
        for inl in scan["inline_images"]:
            comment_id = inl["comment_id"]
            img_url = inl["img_url"]
            original_html = inl["original_html"]
            filename = inl["file_name"]
            content_type = inl["content_type"]

            try:
                # Download via authenticated session
                dl = zd.session.get(img_url, timeout=30, stream=True)
                if not dl.ok:
                    # Try converting relative URL
                    if img_url.startswith('/'):
                        img_url = f"https://{zd.subdomain}.zendesk.com{img_url}"
                        dl = zd.session.get(img_url, timeout=30, stream=True)
                    if not dl.ok:
                        stats["errors"].append(f"#{tid}: download failed for inline {filename} (HTTP {dl.status_code})")
                        continue

                image_data = dl.content
                if not image_data:
                    stats["errors"].append(f"#{tid}: empty download for inline {filename}")
                    continue

                # Upload to Wasabi
                s3_key = wasabi.upload_attachment(
                    ticket_id=tid,
                    attachment_data=image_data,
                    original_filename=filename,
                    content_type=content_type,
                )
                if not s3_key:
                    stats["errors"].append(f"#{tid}: Wasabi upload failed for inline {filename}")
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
                        print(f"   ✓ inline {filename} ({image_size/1024:.0f} KB) → Wasabi + redacted")
                    else:
                        print(f"   ⚠ inline {filename} uploaded but redaction failed")
                        stats["errors"].append(f"#{tid}: AW redact failed for inline {filename}")
                else:
                    stats["errors"].append(f"#{tid}: no Wasabi URL for inline {filename}")

                time.sleep(0.5)  # Agent Workspace API is sensitive to rate

            except Exception as e:
                stats["errors"].append(f"#{tid}: inline {filename}: {e}")
                print(f"   ✗ inline {filename}: {e}")

        # ── Update DB record ─────────────────────────────────────────────
        if ticket_uploaded > 0:
            db = get_db()
            try:
                wasabi_json = json.dumps(s3_keys) if s3_keys else None
                existing = db.query(ProcessedTicket).filter_by(ticket_id=tid).first()
                old_count = existing.attachments_count if existing else 0
                old_size = existing.wasabi_files_size if existing else 0

                # Merge existing s3 keys with new ones
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

    # ── Phase 4: Summary ─────────────────────────────────────────────────
    elapsed = (datetime.utcnow() - start).total_seconds()
    print("\n" + "=" * 60)
    print("🏁 BULK INLINE OFFLOAD COMPLETE")
    print("=" * 60)
    print(f"   Duration: {elapsed/60:.1f} min")
    print(f"   Tickets processed: {stats['tickets_processed']}")
    print(f"   Attachments: {stats['att_uploaded']} uploaded, {stats['att_redacted']} redacted")
    print(f"   Inline images: {stats['inl_uploaded']} uploaded, {stats['inl_redacted']} redacted")
    print(f"   Storage freed: {stats['bytes_freed']/1048576:.1f} MB")
    print(f"   Errors: {len(stats['errors'])}")
    if stats["errors"]:
        print("\n   First 10 errors:")
        for err in stats["errors"][:10]:
            print(f"     • {err}")

    # ── Telegram report ──────────────────────────────────────────────────
    try:
        from telegram_reporter import TelegramReporter
        tr = TelegramReporter()
        msg = (
            f"🗃 *Bulk Inline Offload Complete*\n\n"
            f"⏱ Duration: {elapsed/60:.1f} min\n"
            f"📋 Tickets: {stats['tickets_processed']}\n"
            f"📎 Attachments: {stats['att_uploaded']} ↗ {stats['att_redacted']} 🗑\n"
            f"🖼 Inlines: {stats['inl_uploaded']} ↗ {stats['inl_redacted']} 🗑\n"
            f"💾 Freed: {stats['bytes_freed']/1048576:.1f} MB\n"
            f"❌ Errors: {len(stats['errors'])}"
        )
        tr.send_message(msg)
    except Exception as e:
        print(f"   (Telegram report failed: {e})")


if __name__ == "__main__":
    main()
