"""
Closed ticket backup manager.
Backs up closed Zendesk tickets to a dedicated Wasabi bucket for portability.
"""

    for index, ticket_id in enumerate(candidate_ids, 1):
        files_uploaded = 0
        bytes_uploaded = 0
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

                    closed_dt = self._ticket_closed_datetime(ticket)
                    if not closed_dt:
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

                            max_retries = 3
                            for attempt in range(1, max_retries + 1):
                                fresh_url = content_url
                                if att.get('inline', False):
                                    try:
                                        fresh_comment_resp = self.zendesk.session.get(
                                            f"{self.zendesk.base_url}/tickets/{ticket_id}/comments/{comment_id}.json",
                                            timeout=15,
                                        )
                                        if fresh_comment_resp.ok:
                                            fresh_comment = fresh_comment_resp.json().get('comment', {})
                                            for fresh_att in fresh_comment.get('attachments', []):
                                                if fresh_att.get('id') == attachment_id:
                                                    fresh_url = fresh_att.get('content_url')
                                                    break
                                    except Exception as exc:
                                        logger.warning(f"[TicketBackup] Failed to fetch fresh inline token for ticket #{ticket_id} comment {comment_id}: {exc}")

                                if not fresh_url:
                                    attachment_row['error'] = 'No content_url available'
                                    break

                                try:
                                    blob = self.zendesk.download_attachment(fresh_url)
                                    if blob:
                                        put_key = wasabi.upload_attachment(
                                            ticket_id=ticket_id,
                                            attachment_data=blob,
                                            original_filename=f"att_{attachment_id}_{safe_name}",
                                            content_type=att.get('content_type', 'application/octet-stream'),
                                        )
                                        if put_key:
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
                                            break  # Success, exit retry loop
                                except Exception as exc:
                                    if hasattr(exc, 'response') and exc.response is not None:
                                        status = exc.response.status_code
                                        if status in (401, 403):
                                            logger.error(f"[TicketBackup] Permission error for ticket #{ticket_id} attachment {attachment_id}: HTTP {status}. Check Zendesk API permissions.")
                                            attachment_row['error'] = f"Permission error HTTP {status}"
                                            break
                                    attachment_row['error'] = str(exc)
                                    if attempt == max_retries:
                                        logger.error(f"[TicketBackup] Failed to download attachment (ticket #{ticket_id} attachment {attachment_id}) after {max_retries} attempts: {exc}")
                                    else:
                                        logger.warning(f"[TicketBackup] Retry {attempt} for ticket #{ticket_id} attachment {attachment_id}: {exc}")

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

                    html_key = f"{date_folder}/{ticket_id}_ticket.html"
                    html_blob = self._build_ticket_html(ticket, comments, attachment_manifest).encode('utf-8')
                    wasabi.s3_client.put_object(
                        Bucket=wasabi.bucket_name,
                        Key=html_key,
                        Body=html_blob,
                        ContentType='text/html',
                    )
                    files_uploaded += 1
                    bytes_uploaded += len(html_blob)

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
                                                if fresh_att.get('id') == attachment_id:
                                                    fresh_url = fresh_att.get('content_url')
                                                    break
                                    except Exception as exc:
                                        logger.warning(f"[TicketBackup] Failed to fetch fresh inline token for ticket #{ticket_id} comment {comment_id}: {exc}")

                                if not fresh_url:
                                    attachment_row['error'] = 'No content_url available'
                                    break

                                try:
                                    blob = self.zendesk.download_attachment(fresh_url)
                                    if blob:
                                        put_key = wasabi.upload_attachment(
                                            ticket_id=ticket_id,
                                            attachment_data=blob,
                                            original_filename=f"att_{attachment_id}_{safe_name}",
                                            content_type=att.get('content_type', 'application/octet-stream'),
                                        )
                                        if put_key:
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
                                            break  # Success, exit retry loop
                                except Exception as exc:
                                    # Log permission errors
                                    if hasattr(exc, 'response') and exc.response is not None:
                                        status = exc.response.status_code
                                        if status in (401, 403):
                                            logger.error(f"[TicketBackup] Permission error for ticket #{ticket_id} attachment {attachment_id}: HTTP {status}. Check Zendesk API permissions.")
                                            attachment_row['error'] = f"Permission error HTTP {status}"
                                            break
                                    attachment_row['error'] = str(exc)
                                    if attempt == max_retries:
                                        logger.error(f"[TicketBackup] Failed to download attachment (ticket #{ticket_id} attachment {attachment_id}) after {max_retries} attempts: {exc}")
                                    else:
                                        logger.warning(f"[TicketBackup] Retry {attempt} for ticket #{ticket_id} attachment {attachment_id}: {exc}")

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

                    # HTML export
                    html_key = f"{date_folder}/{ticket_id}_ticket.html"
                    html_blob = self._build_ticket_html(ticket, comments, attachment_manifest).encode('utf-8')
                    wasabi.s3_client.put_object(
                        Bucket=wasabi.bucket_name,
                        Key=html_key,
                        Body=html_blob,
                        ContentType='text/html',
                    )
                    files_uploaded += 1
                    bytes_uploaded += len(html_blob)

            # End of try block for ticket processing
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

    def _build_ticket_html(self, ticket, comments, attachments):
        ticket_id = ticket.get('id', 'Unknown')
        subject = ticket.get('subject', '')
        requester = ticket.get('requester_id', '')
        created = ticket.get('created_at', '')
        status = ticket.get('status', '')
        priority = ticket.get('priority', '')
        html = [
            f"<html><head><meta charset='utf-8'><title>Ticket #{ticket_id}</title></head><body>",
            f"<h2>Ticket #{ticket_id}: {subject}</h2>",
            f"<p><b>Status:</b> {status} &nbsp; <b>Priority:</b> {priority} &nbsp; <b>Requester:</b> {requester} &nbsp; <b>Created:</b> {created}</p>",
            "<hr>"
        ]
        html.append("<h3>Comments:</h3>")
        for c in comments:
            author = c.get('author_id', '')
            created = c.get('created_at', '')
            body = c.get('body', '') or c.get('html_body', '')
            html.append(f"<div style='margin-bottom:18px'><b>Author:</b> {author} &nbsp; <b>Created:</b> {created}<br><div style='margin:8px 0;padding:8px;background:#f6f6f6;border-radius:6px'>{body}</div></div>")
        html.append("<hr><h3>Attachments:</h3>")
        for att in attachments:
            fname = att.get('file_name', '')
            size = att.get('size', 0)
            s3_key = att.get('s3_key', '')
            html.append(f"<div><b>{fname}</b> ({size} bytes) &mdash; S3 Key: {s3_key}</div>")
        html.append("</body></html>")
        return '\n'.join(html)

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
