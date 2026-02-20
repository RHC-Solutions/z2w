#!/usr/bin/env python3
"""
Bulk redact all attachments and inline images from Zendesk tickets that have
already been offloaded to Wasabi.  Frees Zendesk storage.

Uses the Agent Workspace Redaction API (PUT /api/v2/comment_redactions/{comment_id})
which works on BOTH open AND closed/archived tickets.

Safety: only touches tickets present in zendesk_storage_snapshot with total_size > 0
AND that have a matching ProcessedTicket row with wasabi_files set.

Usage:
    python3 bulk_redact_zendesk.py              # dry-run (prints what it would do)
    python3 bulk_redact_zendesk.py --execute    # actually redact
"""
import sys
import re
import time
import json
import logging
from datetime import datetime
from database import get_db, ProcessedTicket, ZendeskStorageSnapshot
from zendesk_client import ZendeskClient

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(message)s')
log = logging.getLogger(__name__)

DRY_RUN = '--execute' not in sys.argv


def main():
    zc = ZendeskClient()
    session = zc.session
    db = get_db()

    rows = (
        db.query(ZendeskStorageSnapshot)
        .filter(ZendeskStorageSnapshot.total_size > 0)
        .order_by(ZendeskStorageSnapshot.total_size.desc())
        .all()
    )

    # Only process tickets that have been offloaded
    targets = []
    for r in rows:
        pt = db.query(ProcessedTicket).filter_by(ticket_id=r.ticket_id).first()
        if pt and pt.wasabi_files and pt.wasabi_files not in ('', '[]') and pt.attachments_count > 0:
            targets.append(r)
    db.close()

    total_tickets = len(targets)
    total_bytes = sum(r.total_size for r in targets)
    mode = "DRY RUN" if DRY_RUN else "EXECUTING"
    log.info(f"=== Bulk Zendesk Redaction — {mode} ===")
    log.info(f"Tickets: {total_tickets}, Estimated storage to free: {total_bytes / 1048576:.1f} MB")
    if DRY_RUN:
        log.info("Pass --execute to actually perform redactions.")
    print()

    stats = {
        'tickets': 0,
        'attachments_redacted': 0,
        'inlines_redacted': 0,
        'bytes_freed': 0,
        'errors': [],
    }

    for idx, snap in enumerate(targets):
        tid = snap.ticket_id
        log.info(
            f"[{idx + 1}/{total_tickets}] Ticket #{tid} — "
            f"{snap.attach_count}att + {snap.inline_count}inl, "
            f"{snap.total_size:,} bytes, status={snap.zd_status}"
        )

        try:
            # Fetch comments
            resp = _get_with_retry(session, f"{zc.base_url}/tickets/{tid}/comments.json")
            if not resp or not resp.ok:
                stats['errors'].append(f"#{tid}: HTTP {getattr(resp, 'status_code', '?')} fetching comments")
                continue
            comments = resp.json().get('comments', [])

            for comment in comments:
                cid = comment['id']

                # ── 1) Redact regular attachments via external_attachment_urls ──
                non_redacted = [
                    a for a in comment.get('attachments', [])
                    if not a.get('file_name', '').lower().endswith('redacted.txt')
                ]
                if non_redacted:
                    urls_to_redact = [a['content_url'] for a in non_redacted if a.get('content_url')]
                    total_att_size = sum(a.get('size', 0) for a in non_redacted)

                    if DRY_RUN:
                        for a in non_redacted:
                            log.info(f"  [DRY] Would redact attachment {a['file_name']} ({a.get('size', 0):,} bytes)")
                        stats['attachments_redacted'] += len(non_redacted)
                        stats['bytes_freed'] += total_att_size
                    elif urls_to_redact:
                        url = f"{zc.base_url}/comment_redactions/{cid}.json"
                        payload = {
                            "ticket_id": tid,
                            "external_attachment_urls": urls_to_redact,
                        }
                        r = _put_with_retry(session, url, payload)
                        if r and r.ok:
                            stats['attachments_redacted'] += len(urls_to_redact)
                            stats['bytes_freed'] += total_att_size
                            names = ', '.join(a['file_name'] for a in non_redacted)
                            log.info(f"  ✓ Redacted {len(urls_to_redact)} attachment(s): {names}")
                        else:
                            code = getattr(r, 'status_code', '?')
                            body = (r.text[:200] if r else 'no response')
                            stats['errors'].append(f"#{tid} cid={cid} attachments: HTTP {code}")
                            log.warning(f"  ✗ Failed to redact attachments: {code} {body}")
                        time.sleep(0.2)

                # ── 2) Redact inline images via html_body + redact attribute ──
                html_body = comment.get('html_body', '') or ''
                img_tags = re.findall(
                    r'<img[^>]+src="https://[^"]*zendesk[^"]*attachments[^"]*"[^>]*>',
                    html_body, re.IGNORECASE,
                )
                if not img_tags:
                    continue

                if DRY_RUN:
                    for img_tag in img_tags:
                        name_m = re.search(r'name=([^&"]+)', img_tag)
                        fname = name_m.group(1) if name_m else 'inline_image'
                        log.info(f"  [DRY] Would redact inline image '{fname}'")
                        stats['inlines_redacted'] += 1
                    continue

                # Redact one image at a time (html_body changes after each)
                for _ in img_tags:
                    # Re-fetch current state
                    refetch = _get_with_retry(session, f"{zc.base_url}/tickets/{tid}/comments.json")
                    if not refetch or not refetch.ok:
                        break
                    cur_comment = next(
                        (c for c in refetch.json().get('comments', []) if c['id'] == cid), None
                    )
                    if not cur_comment:
                        break
                    cur_html = cur_comment.get('html_body', '') or ''

                    remaining = re.findall(
                        r'<img[^>]+src="https://[^"]*zendesk[^"]*attachments[^"]*"[^>]*>',
                        cur_html, re.IGNORECASE,
                    )
                    if not remaining:
                        break

                    target = remaining[0]
                    name_m = re.search(r'name=([^&"]+)', target)
                    fname = name_m.group(1) if name_m else 'inline_image'

                    # Add redact attribute
                    if target.rstrip().endswith('/>'):
                        redacted = target.rstrip()[:-2].rstrip() + ' redact />'
                    else:
                        redacted = target.rstrip()[:-1].rstrip() + ' redact>'

                    modified = cur_html.replace(target, redacted, 1)
                    if modified == cur_html:
                        log.warning(f"  ✗ Could not locate img tag for '{fname}' in comment {cid}")
                        break

                    url = f"{zc.base_url}/comment_redactions/{cid}.json"
                    payload = {"ticket_id": tid, "html_body": modified}
                    r = _put_with_retry(session, url, payload)
                    if r and r.ok:
                        stats['inlines_redacted'] += 1
                        log.info(f"  ✓ Redacted inline image '{fname}'")
                    else:
                        code = getattr(r, 'status_code', '?')
                        body = (r.text[:200] if r else 'no response')
                        stats['errors'].append(f"#{tid} inline '{fname}' cid={cid}: HTTP {code}")
                        log.warning(f"  ✗ Failed to redact '{fname}': {code} {body}")
                        break
                    time.sleep(0.2)

            stats['tickets'] += 1

        except Exception as e:
            stats['errors'].append(f"#{tid}: {e}")
            log.error(f"  ✗ Exception: {e}")

    # Summary
    print()
    log.info("=" * 60)
    log.info(f"{'DRY RUN' if DRY_RUN else 'EXECUTION'} COMPLETE")
    log.info(f"Tickets processed:     {stats['tickets']}/{total_tickets}")
    log.info(f"Attachments redacted:  {stats['attachments_redacted']}")
    log.info(f"Inline imgs redacted:  {stats['inlines_redacted']}")
    log.info(f"Estimated freed:       {stats['bytes_freed'] / 1048576:.1f} MB")
    log.info(f"Errors:                {len(stats['errors'])}")
    if stats['errors']:
        for e in stats['errors'][:30]:
            log.info(f"  - {e}")
    log.info("=" * 60)


def _get_with_retry(session, url, retries=2):
    for attempt in range(retries + 1):
        try:
            r = session.get(url, timeout=20)
            if r.status_code == 429:
                wait = int(r.headers.get('Retry-After', 30))
                log.warning(f"  Rate limited, waiting {wait}s")
                time.sleep(wait)
                continue
            return r
        except Exception as e:
            if attempt == retries:
                log.error(f"  GET failed after {retries + 1} attempts: {e}")
                return None
            time.sleep(2)
    return None


def _put_with_retry(session, url, payload, retries=2):
    for attempt in range(retries + 1):
        try:
            r = session.put(url, json=payload, timeout=30)
            if r.status_code == 429:
                wait = int(r.headers.get('Retry-After', 30))
                log.warning(f"  Rate limited, waiting {wait}s")
                time.sleep(wait)
                continue
            return r
        except Exception as e:
            if attempt == retries:
                log.error(f"  PUT failed after {retries + 1} attempts: {e}")
                return None
            time.sleep(2)
    return None


if __name__ == '__main__':
    main()
