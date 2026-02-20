"""
Closed ticket backup manager.
Backs up closed Zendesk tickets to a dedicated Wasabi bucket for portability.
"""
import json
import logging
import re
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from database import (
    get_db,
    Setting,
    ZendeskTicketCache,
    TicketBackupItem,
    TicketBackupRun,
)
from zendesk_client import ZendeskClient
from wasabi_client import WasabiClient
from config import (
    WASABI_ACCESS_KEY,
    WASABI_SECRET_KEY,
    TICKET_BACKUP_ENDPOINT,
    TICKET_BACKUP_BUCKET,
)

logger = logging.getLogger('zendesk_offloader')


class TicketBackupManager:
    """Backup closed tickets + attachments to Wasabi in portable JSON format."""

    def __init__(self):
        self.zendesk = ZendeskClient()

    @staticmethod
    def _safe_filename(name: str) -> str:
        name = (name or "file").strip()
        name = re.sub(r"[^A-Za-z0-9._-]+", "_", name)
        return name[:180] or "file"

    def _get_setting_or_default(self, db, key: str, default_value: str) -> str:
        row = db.query(Setting).filter_by(key=key).first()
        if row and row.value is not None and str(row.value).strip() != "":
            return str(row.value).strip()
        return default_value

    def _build_wasabi_client(self) -> WasabiClient:
        db = get_db()
        try:
            endpoint = self._get_setting_or_default(db, 'TICKET_BACKUP_ENDPOINT', TICKET_BACKUP_ENDPOINT)
            bucket = self._get_setting_or_default(db, 'TICKET_BACKUP_BUCKET', TICKET_BACKUP_BUCKET)
            access_key = self._get_setting_or_default(db, 'WASABI_ACCESS_KEY', WASABI_ACCESS_KEY)
            secret_key = self._get_setting_or_default(db, 'WASABI_SECRET_KEY', WASABI_SECRET_KEY)
        finally:
            db.close()

        endpoint = endpoint.strip()
        if endpoint and not endpoint.startswith('http'):
            endpoint = f"https://{endpoint}"

        return WasabiClient(
            endpoint=endpoint,
            access_key=access_key,
            secret_key=secret_key,
            bucket_name=bucket,
        )

    def _collect_closed_candidates(self, db) -> List[int]:
        cached_closed = db.query(ZendeskTicketCache.ticket_id).filter(
            ZendeskTicketCache.status == 'closed'
        ).all()
        cached_closed_ids = [row[0] for row in cached_closed]

        done_ids = set(
            row[0] for row in db.query(TicketBackupItem.ticket_id).filter(
                TicketBackupItem.backup_status == 'success'
            ).all()
        )

        return [ticket_id for ticket_id in cached_closed_ids if ticket_id not in done_ids]

    def _upsert_item(
        self,
        db,
        ticket_id: int,
        closed_at: Optional[datetime],
        backup_status: str,
        s3_prefix: Optional[str],
        files_count: int,
        total_bytes: int,
        last_error: Optional[str],
    ) -> None:
        row = db.query(TicketBackupItem).filter_by(ticket_id=ticket_id).first()
        if not row:
            row = TicketBackupItem(ticket_id=ticket_id)
            db.add(row)

        row.closed_at = closed_at
        row.last_backup_at = datetime.utcnow()
        row.backup_status = backup_status
        row.s3_prefix = s3_prefix
        row.files_count = files_count
        row.total_bytes = total_bytes
        row.last_error = last_error
        row.updated_at = datetime.utcnow()

    def _ticket_closed_datetime(self, ticket: Dict) -> Optional[datetime]:
        """Return the ticket's closed_at timestamp, falling back to updated_at."""
        stamp = ticket.get('closed_at') or ticket.get('updated_at')
        if not stamp:
            return None
        try:
            if stamp.endswith('Z'):
                stamp = stamp[:-1]
            return datetime.fromisoformat(stamp)
        except Exception:
            return None

    def _build_export_document(self, ticket: Dict, comments: List[Dict], attachment_manifest: List[Dict]) -> Dict:
        return {
            "exported_at_utc": datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ'),
            "source": {
                "platform": "zendesk",
                "subdomain": self.zendesk.subdomain,
                "format": "json",
            },
            "ticket": ticket,
            "comments": comments,
            "attachments": attachment_manifest,
        }

    def backup_closed_tickets(self, limit: int = 0) -> Dict:
        """
        Backup closed tickets into Wasabi bucket using structure:
            YYYYMMDD/TICKETID_*
        """
        started_at = datetime.utcnow()
        run_stats = {
            "run_date": started_at,
            "tickets_scanned": 0,
            "tickets_backed_up": 0,
            "files_uploaded": 0,
            "bytes_uploaded": 0,
            "errors": [],
            "details": [],
        }

        wasabi = self._build_wasabi_client()

        db = get_db()
        try:
            candidate_ids = self._collect_closed_candidates(db)
            if limit and limit > 0:
                candidate_ids = candidate_ids[:limit]

            run_stats['tickets_scanned'] = len(candidate_ids)

            for index, ticket_id in enumerate(candidate_ids, 1):
                files_uploaded = 0
                bytes_uploaded = 0
                try:
                    ticket_resp = self.zendesk.session.get(
                        f"{self.zendesk.base_url}/tickets/{ticket_id}.json",
                        timeout=30,
                    )
                    if not ticket_resp.ok:
                        self._upsert_item(
                            db=db,
                            ticket_id=ticket_id,
                            closed_at=None,
                            backup_status='failed',
                            s3_prefix=f"{datetime.utcnow().strftime('%Y%m%d')}/{ticket_id}",
                            files_count=0,
                            total_bytes=0,
                            last_error=f"ticket fetch failed HTTP {ticket_resp.status_code}",
                        )
                        run_stats['errors'].append(f"#{ticket_id}: ticket fetch HTTP {ticket_resp.status_code}")
                        db.commit()
                        continue

                    ticket = ticket_resp.json().get('ticket', {})
                    if ticket.get('status') != 'closed':
                        self._upsert_item(
                            db=db,
                            ticket_id=ticket_id,
                            closed_at=self._ticket_closed_datetime(ticket),
                            backup_status='skipped',
                            s3_prefix=f"{datetime.utcnow().strftime('%Y%m%d')}/{ticket_id}",
                            files_count=0,
                            total_bytes=0,
                            last_error='ticket no longer closed',
                        )
                        db.commit()
                        continue

                    # Folder = ticket's closed date (YYYYMMDD)
                    closed_dt = self._ticket_closed_datetime(ticket)
                    if not closed_dt:
                        # fallback: try updated_at, then today
                        ua = ticket.get('updated_at', '')
                        if ua:
                            try:
                                closed_dt = datetime.fromisoformat(ua.replace('Z', ''))
                            except Exception:
                                pass
                    date_folder = (closed_dt or datetime.utcnow()).strftime('%Y%m%d')

                    comments_resp = self.zendesk.session.get(
                        f"{self.zendesk.base_url}/tickets/{ticket_id}/comments.json",
                        timeout=30,
                    )
                    if not comments_resp.ok:
                        self._upsert_item(
                            db=db,
                            ticket_id=ticket_id,
                            closed_at=self._ticket_closed_datetime(ticket),
                            backup_status='failed',
                            s3_prefix=f"{date_folder}/{ticket_id}",
                            files_count=0,
                            total_bytes=0,
                            last_error=f"comments fetch failed HTTP {comments_resp.status_code}",
                        )
                        run_stats['errors'].append(f"#{ticket_id}: comments fetch HTTP {comments_resp.status_code}")
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

                            attachment_row = {
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

                            if content_url:
                                try:
                                    blob = self.zendesk.download_attachment(content_url)
                                    if blob:
                                        put_key = wasabi.upload_attachment(
                                            ticket_id=ticket_id,
                                            attachment_data=blob,
                                            original_filename=f"att_{attachment_id}_{safe_name}",
                                            content_type=att.get('content_type', 'application/octet-stream'),
                                        )
                                        if put_key:
                                            # enforce requested exact structure YYYYMMDD/TICKETID_* by custom put
                                            # if upload_attachment chose a different name, put again to exact key
                                            if put_key != s3_key:
                                                wasabi.s3_client.put_object(
                                                    Bucket=wasabi.bucket_name,
                                                    Key=s3_key,
                                                    Body=blob,
                                                    ContentType=att.get('content_type', 'application/octet-stream'),
                                                )
                                            attachment_row['uploaded'] = True
                                            files_uploaded += 1
                                            bytes_uploaded += len(blob)
                                except Exception as exc:
                                    attachment_row['error'] = str(exc)

                            attachment_manifest.append(attachment_row)

                    export_doc = self._build_export_document(ticket, comments, attachment_manifest)
                    json_blob = json.dumps(export_doc, ensure_ascii=False, default=str).encode('utf-8')
                    json_key = f"{date_folder}/{ticket_id}_ticket.json"
                    wasabi.s3_client.put_object(
                        Bucket=wasabi.bucket_name,
                        Key=json_key,
                        Body=json_blob,
                        ContentType='application/json',
                    )
                    files_uploaded += 1
                    bytes_uploaded += len(json_blob)

                    self._upsert_item(
                        db=db,
                        ticket_id=ticket_id,
                        closed_at=self._ticket_closed_datetime(ticket),
                        backup_status='success',
                        s3_prefix=f"{date_folder}/{ticket_id}",
                        files_count=files_uploaded,
                        total_bytes=bytes_uploaded,
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
                            db=db,
                            ticket_id=ticket_id,
                            closed_at=None,
                            backup_status='failed',
                            s3_prefix=f"{date_folder}/{ticket_id}",
                            files_count=files_uploaded,
                            total_bytes=bytes_uploaded,
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
                status='completed' if len(run_stats['errors']) == 0 else 'completed_with_errors',
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
