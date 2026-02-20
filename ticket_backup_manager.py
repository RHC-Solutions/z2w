"""
Closed ticket backup manager.
Backs up closed Zendesk tickets to a dedicated Wasabi bucket for portability.
"""
import json
import logging
import re
from datetime import datetime
from typing import Dict, List, Optional

from database import get_db, TicketBackupItem, TicketBackupRun
from zendesk_client import ZendeskClient
from wasabi_client import WasabiClient

logger = logging.getLogger('zendesk_offloader')


class TicketBackupManager:
    """Back up closed Zendesk ticket metadata + attachments to a Wasabi bucket."""

    def __init__(self):
        from config import (
            ZENDESK_SUBDOMAIN, ZENDESK_EMAIL, ZENDESK_API_TOKEN,
            WASABI_ACCESS_KEY, WASABI_SECRET_KEY,
            TICKET_BACKUP_ENDPOINT, TICKET_BACKUP_BUCKET,
        )
        self._zd_subdomain = ZENDESK_SUBDOMAIN
        self._zd_email = ZENDESK_EMAIL
        self._zd_token = ZENDESK_API_TOKEN
        self._wb_access_key = WASABI_ACCESS_KEY
        self._wb_secret_key = WASABI_SECRET_KEY
        self._wb_endpoint = TICKET_BACKUP_ENDPOINT
        self._wb_bucket = TICKET_BACKUP_BUCKET
        self.zendesk: Optional[ZendeskClient] = None

    # ── private helpers ────────────────────────────────────────────────────

    def _build_wasabi_client(self) -> WasabiClient:
        from config import (
            WASABI_ACCESS_KEY, WASABI_SECRET_KEY,
            TICKET_BACKUP_ENDPOINT, TICKET_BACKUP_BUCKET,
        )
        endpoint = TICKET_BACKUP_ENDPOINT or self._wb_endpoint
        if not endpoint.startswith('http'):
            endpoint = f'https://{endpoint}'
        return WasabiClient(
            endpoint=endpoint,
            access_key=WASABI_ACCESS_KEY or self._wb_access_key,
            secret_key=WASABI_SECRET_KEY or self._wb_secret_key,
            bucket_name=TICKET_BACKUP_BUCKET or self._wb_bucket,
        )

    def _get_zendesk(self) -> ZendeskClient:
        from config import ZENDESK_SUBDOMAIN, ZENDESK_EMAIL, ZENDESK_API_TOKEN
        if self.zendesk is None:
            self.zendesk = ZendeskClient(
                subdomain=ZENDESK_SUBDOMAIN or self._zd_subdomain,
                email=ZENDESK_EMAIL or self._zd_email,
                api_token=ZENDESK_API_TOKEN or self._zd_token,
            )
        return self.zendesk

    def _collect_closed_candidates(self, db) -> List[int]:
        """Return ticket IDs that are closed in ZD cache but not yet successfully backed up."""
        from database import ZendeskTicketCache
        from sqlalchemy.orm import aliased
        already_done = {
            row.ticket_id
            for row in db.query(TicketBackupItem.ticket_id).filter(
                TicketBackupItem.backup_status == 'success'
            ).all()
        }
        rows = db.query(ZendeskTicketCache.ticket_id).filter(
            ZendeskTicketCache.status == 'closed'
        ).all()
        return [r.ticket_id for r in rows if r.ticket_id not in already_done]

    def _upsert_item(
        self, db, ticket_id: int, closed_at, backup_status: str,
        s3_prefix: str, files_count: int, total_bytes: int, last_error
    ):
        row = db.query(TicketBackupItem).filter_by(ticket_id=ticket_id).first()
        if row is None:
            row = TicketBackupItem(ticket_id=ticket_id)
            db.add(row)
        row.backup_status = backup_status
        row.last_backup_at = datetime.utcnow()
        row.s3_prefix = s3_prefix
        row.files_count = files_count
        row.total_bytes = total_bytes
        row.last_error = str(last_error) if last_error else None
        if closed_at and row.closed_at is None:
            row.closed_at = closed_at

    @staticmethod
    def _ticket_closed_datetime(ticket: dict) -> Optional[datetime]:
        for field in ('closed_at', 'updated_at', 'created_at'):
            val = ticket.get(field)
            if val:
                try:
                    return datetime.fromisoformat(val.replace('Z', ''))
                except Exception:
                    pass
        return None

    @staticmethod
    def _safe_filename(name: str) -> str:
        name = re.sub(r'[^\w.\-]', '_', name)
        return name[:120] if len(name) > 120 else name

    def _build_export_document(self, ticket: dict, comments: list, attachments: list) -> dict:
        return {
            'ticket': ticket,
            'comments': comments,
            'attachments': attachments,
            'exported_at': datetime.utcnow().isoformat(),
        }

    def _build_ticket_html(self, ticket: dict, comments: list, attachments: list) -> str:
        ticket_id = ticket.get('id', 'Unknown')
        subject = ticket.get('subject', '')
        requester = ticket.get('requester_id', '')
        created = ticket.get('created_at', '')
        status = ticket.get('status', '')
        priority = ticket.get('priority', '')
        html = [
            "<html><head><meta charset='utf-8'>"
            f"<title>Ticket #{ticket_id}</title></head><body>",
            f"<h2>Ticket #{ticket_id}: {subject}</h2>",
            f"<p><b>Status:</b> {status} &nbsp; <b>Priority:</b> {priority}"
            f" &nbsp; <b>Requester:</b> {requester} &nbsp; <b>Created:</b> {created}</p>",
            "<hr>",
        ]
        html.append("<h3>Comments:</h3>")
        for c in comments:
            author = c.get('author_id', '')
            c_created = c.get('created_at', '')
            body = c.get('html_body') or c.get('body', '')
            html.append(
                f"<div style='margin-bottom:18px'><b>Author:</b> {author}"
                f" &nbsp; <b>Created:</b> {c_created}<br>"
                f"<div style='margin:8px 0;padding:8px;background:#f6f6f6;"
                f"border-radius:6px'>{body}</div></div>"
            )
        html.append("<hr><h3>Attachments:</h3>")
        for att in attachments:
            fname = att.get('file_name', '')
            size = att.get('size', 0)
            s3_key = att.get('s3_key', '')
            html.append(f"<div><b>{fname}</b> ({size} bytes) &mdash; S3 Key: {s3_key}</div>")
        html.append("</body></html>")
        return '\n'.join(html)

    # ── public API ─────────────────────────────────────────────────────────

    def backfill_html(self, limit: int = 0) -> Dict:
        """
        Generate and upload missing HTML files for tickets that only have JSON in the bucket.
        Reads the existing JSON from Wasabi (no Zendesk API calls needed).
        """
        wasabi = self._build_wasabi_client()
        paginator = wasabi.s3_client.get_paginator('list_objects_v2')

        # Collect all keys grouped by prefix (date/ticket_id)
        json_keys: Dict[str, str] = {}   # ticket_id_str -> json_key
        html_keys: set = set()           # json_key with .html counterpart

        logger.info("[BackfillHTML] Scanning bucket for existing objects…")
        for page in paginator.paginate(Bucket=wasabi.bucket_name):
            for obj in page.get('Contents', []):
                key = obj['Key']
                if key.endswith('_ticket.json'):
                    # e.g. 20251213/54_ticket.json  -> ticket_id=54
                    base = key[:-len('_ticket.json')]  # "20251213/54"
                    tid_str = base.split('/')[-1]      # "54"
                    json_keys[tid_str] = key
                elif key.endswith('_ticket.html'):
                    base = key[:-len('_ticket.html')]
                    tid_str = base.split('/')[-1]
                    html_keys.add(tid_str)

        missing = {tid: key for tid, key in json_keys.items() if tid not in html_keys}
        total = len(missing)
        logger.info(f"[BackfillHTML] {total} tickets need HTML (have JSON, no HTML)")

        if limit > 0:
            items = list(missing.items())[:limit]
        else:
            items = list(missing.items())

        done = 0
        errors = 0
        for tid_str, json_key in items:
            try:
                resp = wasabi.s3_client.get_object(Bucket=wasabi.bucket_name, Key=json_key)
                doc = json.loads(resp['Body'].read().decode('utf-8'))
                ticket = doc.get('ticket', {})
                comments = doc.get('comments', [])
                attachments = doc.get('attachments', [])

                html_key = json_key.replace('_ticket.json', '_ticket.html')
                html_blob = self._build_ticket_html(ticket, comments, attachments).encode('utf-8')
                wasabi.s3_client.put_object(
                    Bucket=wasabi.bucket_name,
                    Key=html_key,
                    Body=html_blob,
                    ContentType='text/html',
                )
                done += 1
                if done % 500 == 0:
                    logger.info(f"[BackfillHTML] progress {done}/{len(items)}…")
            except Exception as exc:
                errors += 1
                logger.warning(f"[BackfillHTML] #{tid_str}: {exc}")

        # Update files_count for affected backup items
        if done > 0:
            db = get_db()
            try:
                from database import TicketBackupItem as TBI
                affected_ids = [int(t) for t in missing.keys() if t.isdigit()]
                if limit > 0:
                    affected_ids = affected_ids[:limit]
                db.query(TBI).filter(TBI.ticket_id.in_(affected_ids)).update(
                    {TBI.files_count: TBI.files_count + 1},
                    synchronize_session=False,
                )
                db.commit()
            except Exception as e:
                db.rollback()
                logger.warning(f"[BackfillHTML] files_count update failed: {e}")
            finally:
                db.close()

        logger.info(f"[BackfillHTML] Done — {done} HTML files uploaded, {errors} errors")
        return {'total': total, 'done': done, 'errors': errors}

    def backup_closed_tickets(self, limit: int = 0) -> Dict:
        """
        Back up closed tickets to Wasabi bucket using structure:
            YYYYMMDD/TICKETID_*
        Returns a run_stats dict.
        """
        from config import TICKET_BACKUP_DAILY_LIMIT
        started_at = datetime.utcnow()
        run_stats: Dict = {
            "run_date": started_at,
            "tickets_scanned": 0,
            "tickets_backed_up": 0,
            "files_uploaded": 0,
            "bytes_uploaded": 0,
            "errors": [],
            "details": [],
        }

        effective_limit = limit or TICKET_BACKUP_DAILY_LIMIT or 0
        wasabi = self._build_wasabi_client()
        zd = self._get_zendesk()

        db = get_db()
        try:
            candidate_ids = self._collect_closed_candidates(db)
            if effective_limit > 0:
                candidate_ids = candidate_ids[:effective_limit]

            run_stats['tickets_scanned'] = len(candidate_ids)

            for index, ticket_id in enumerate(candidate_ids, 1):
                files_uploaded = 0
                bytes_uploaded = 0
                date_folder = datetime.utcnow().strftime('%Y%m%d')
                try:
                    ticket_resp = zd.session.get(
                        f"{zd.base_url}/tickets/{ticket_id}.json",
                        timeout=30,
                    )
                    if not ticket_resp.ok:
                        sc = ticket_resp.status_code
                        # 404 = deleted or merged ticket — skip permanently, not a failure
                        if sc == 404:
                            logger.info(f"[TicketBackup] #{ticket_id}: not found in Zendesk (deleted/merged) — skipping")
                            self._upsert_item(
                                db=db, ticket_id=ticket_id, closed_at=None,
                                backup_status='skipped',
                                s3_prefix=f"{date_folder}/{ticket_id}",
                                files_count=0, total_bytes=0,
                                last_error='ticket not found in Zendesk (deleted or merged)',
                            )
                        else:
                            self._upsert_item(
                                db=db, ticket_id=ticket_id, closed_at=None,
                                backup_status='failed',
                                s3_prefix=f"{date_folder}/{ticket_id}",
                                files_count=0, total_bytes=0,
                                last_error=f"ticket fetch failed HTTP {sc}",
                            )
                            run_stats['errors'].append(
                                f"#{ticket_id}: ticket fetch HTTP {sc}"
                            )
                        db.commit()
                        continue

                    ticket = ticket_resp.json().get('ticket', {})
                    if ticket.get('status') != 'closed':
                        self._upsert_item(
                            db=db, ticket_id=ticket_id,
                            closed_at=self._ticket_closed_datetime(ticket),
                            backup_status='skipped',
                            s3_prefix=f"{date_folder}/{ticket_id}",
                            files_count=0, total_bytes=0,
                            last_error='ticket no longer closed',
                        )
                        db.commit()
                        continue

                    closed_dt = self._ticket_closed_datetime(ticket)
                    date_folder = (closed_dt or datetime.utcnow()).strftime('%Y%m%d')

                    comments_resp = zd.session.get(
                        f"{zd.base_url}/tickets/{ticket_id}/comments.json",
                        timeout=30,
                    )
                    if not comments_resp.ok:
                        self._upsert_item(
                            db=db, ticket_id=ticket_id,
                            closed_at=self._ticket_closed_datetime(ticket),
                            backup_status='failed',
                            s3_prefix=f"{date_folder}/{ticket_id}",
                            files_count=0, total_bytes=0,
                            last_error=f"comments fetch failed HTTP {comments_resp.status_code}",
                        )
                        run_stats['errors'].append(
                            f"#{ticket_id}: comments fetch HTTP {comments_resp.status_code}"
                        )
                        db.commit()
                        continue

                    comments = comments_resp.json().get('comments', [])

                    attachment_manifest: List[Dict] = []
                    for comment in comments:
                        comment_id = comment.get('id')
                        for att in comment.get('attachments', []):
                            file_name = att.get('file_name', '')
                            if file_name.lower() == 'redacted.txt':
                                continue
                            content_url = att.get('content_url')
                            attachment_id = att.get('id')
                            safe_name = self._safe_filename(file_name)
                            s3_key = f"{date_folder}/{ticket_id}_att_{attachment_id}_{safe_name}"

                            attachment_row: Dict = {
                                "attachment_id": attachment_id,
                                "comment_id": comment_id,
                                "file_name": file_name,
                                "content_type": att.get('content_type', 'application/octet-stream'),
                                "size": att.get('size', 0),
                                "inline": att.get('inline', False),
                                "zendesk_content_url": content_url,
                                "s3_key": s3_key,
                                "uploaded": False,
                            }

                            max_retries = 3
                            for attempt in range(1, max_retries + 1):
                                fresh_url = content_url
                                if att.get('inline', False):
                                    try:
                                        fresh_resp = zd.session.get(
                                            f"{zd.base_url}/tickets/{ticket_id}/comments/{comment_id}.json",
                                            timeout=15,
                                        )
                                        if fresh_resp.ok:
                                            fresh_comment = fresh_resp.json().get('comment', {})
                                            for fresh_att in fresh_comment.get('attachments', []):
                                                if fresh_att.get('id') == attachment_id:
                                                    fresh_url = fresh_att.get('content_url')
                                                    break
                                    except Exception as exc:
                                        logger.warning(
                                            f"[TicketBackup] Fresh inline token failed #{ticket_id} "
                                            f"comment {comment_id}: {exc}"
                                        )

                                if not fresh_url:
                                    attachment_row['error'] = 'No content_url available'
                                    break

                                try:
                                    blob = zd.download_attachment(fresh_url)
                                    if blob:
                                        wasabi.s3_client.put_object(
                                            Bucket=wasabi.bucket_name,
                                            Key=s3_key,
                                            Body=blob,
                                            ContentType=att.get(
                                                'content_type', 'application/octet-stream'
                                            ),
                                        )
                                        attachment_row['uploaded'] = True
                                        files_uploaded += 1
                                        bytes_uploaded += len(blob)
                                        break
                                except Exception as exc:
                                    if hasattr(exc, 'response') and exc.response is not None:
                                        status_code = exc.response.status_code
                                        if status_code in (401, 403):
                                            logger.error(
                                                f"[TicketBackup] Permission error #{ticket_id} "
                                                f"attachment {attachment_id}: HTTP {status_code}"
                                            )
                                            attachment_row['error'] = f"Permission error HTTP {status_code}"
                                            break
                                    attachment_row['error'] = str(exc)
                                    if attempt == max_retries:
                                        logger.error(
                                            f"[TicketBackup] Failed #{ticket_id} att {attachment_id} "
                                            f"after {max_retries} attempts: {exc}"
                                        )
                                    else:
                                        logger.warning(
                                            f"[TicketBackup] Retry {attempt} #{ticket_id} "
                                            f"att {attachment_id}: {exc}"
                                        )

                            attachment_manifest.append(attachment_row)

                    # Upload JSON export
                    export_doc = self._build_export_document(ticket, comments, attachment_manifest)
                    json_blob = json.dumps(export_doc, ensure_ascii=False, default=str).encode('utf-8')
                    json_key = f"{date_folder}/{ticket_id}_ticket.json"
                    wasabi.s3_client.put_object(
                        Bucket=wasabi.bucket_name, Key=json_key,
                        Body=json_blob, ContentType='application/json',
                    )
                    files_uploaded += 1
                    bytes_uploaded += len(json_blob)

                    # Upload HTML export
                    html_key = f"{date_folder}/{ticket_id}_ticket.html"
                    html_blob = self._build_ticket_html(
                        ticket, comments, attachment_manifest
                    ).encode('utf-8')
                    wasabi.s3_client.put_object(
                        Bucket=wasabi.bucket_name, Key=html_key,
                        Body=html_blob, ContentType='text/html',
                    )
                    files_uploaded += 1
                    bytes_uploaded += len(html_blob)

                    self._upsert_item(
                        db=db, ticket_id=ticket_id,
                        closed_at=self._ticket_closed_datetime(ticket),
                        backup_status='success',
                        s3_prefix=f"{date_folder}/{ticket_id}",
                        files_count=files_uploaded, total_bytes=bytes_uploaded,
                        last_error=None,
                    )
                    db.commit()

                    run_stats['tickets_backed_up'] += 1
                    run_stats['files_uploaded'] += files_uploaded
                    run_stats['bytes_uploaded'] += bytes_uploaded
                    run_stats['details'].append({
                        "ticket_id": ticket_id,
                        "files_uploaded": files_uploaded,
                        "bytes_uploaded": bytes_uploaded,
                        "json_key": json_key,
                    })

                except Exception as exc:
                    db.rollback()
                    message = f"#{ticket_id}: {exc}"
                    run_stats['errors'].append(message)
                    logger.error(f"[TicketBackup] {message}", exc_info=True)
                    try:
                        self._upsert_item(
                            db=db, ticket_id=ticket_id, closed_at=None,
                            backup_status='failed',
                            s3_prefix=f"{date_folder}/{ticket_id}",
                            files_count=files_uploaded, total_bytes=bytes_uploaded,
                            last_error=str(exc),
                        )
                        db.commit()
                    except Exception:
                        db.rollback()

                if index % 25 == 0:
                    logger.info(
                        f"[TicketBackup] Progress {index}/{len(candidate_ids)} — "
                        f"backed up {run_stats['tickets_backed_up']}"
                    )

            run_row = TicketBackupRun(
                run_date=started_at,
                tickets_scanned=run_stats['tickets_scanned'],
                tickets_backed_up=run_stats['tickets_backed_up'],
                files_uploaded=run_stats['files_uploaded'],
                bytes_uploaded=run_stats['bytes_uploaded'],
                errors_count=len(run_stats['errors']),
                status='completed' if not run_stats['errors'] else 'completed_with_errors',
                details=json.dumps({
                    "errors": run_stats['errors'][:200],
                    "details": run_stats['details'][:200],
                }),
            )
            db.add(run_row)
            db.commit()

        finally:
            db.close()

        return run_stats
