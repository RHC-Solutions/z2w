"""
Zendesk API client for fetching tickets and attachments
"""
import requests
import base64
import re
import time
import logging
from typing import List, Dict, Optional
from config import ZENDESK_SUBDOMAIN, ZENDESK_EMAIL, ZENDESK_API_TOKEN

# Get logger
logger = logging.getLogger('zendesk_offloader')

class ZendeskClient:
    """Client for interacting with Zendesk API"""
    
    def __init__(self):
        self.subdomain = ZENDESK_SUBDOMAIN
        self.email = ZENDESK_EMAIL
        self.api_token = ZENDESK_API_TOKEN
        self._session = None
        
        # Only set base_url if subdomain is configured
        if self.subdomain:
            self.base_url = f"https://{self.subdomain}.zendesk.com/api/v2"
        else:
            self.base_url = None
    
    def _get_session(self):
        """Lazy initialization of requests session"""
        if self._session is None:
            if not self.subdomain or not self.email or not self.api_token:
                raise ValueError("Zendesk credentials not configured. Please set ZENDESK_SUBDOMAIN, ZENDESK_EMAIL, and ZENDESK_API_TOKEN in .env file")
            
            self._session = requests.Session()
            
            # Set up authentication
            credentials = f"{self.email}/token:{self.api_token}"
            encoded_credentials = base64.b64encode(credentials.encode()).decode()
            self._session.headers.update({
                "Authorization": f"Basic {encoded_credentials}",
                "Content-Type": "application/json"
            })
        return self._session
    
    @property
    def session(self):
        """Property to access session with lazy initialization"""
        return self._get_session()
    
    def get_all_tickets(self, status: str = "all") -> List[Dict]:
        """
        Get all tickets from Zendesk using the List Tickets endpoint with cursor-based pagination
        Returns list of ticket dictionaries
        """
        if not self.base_url:
            print("ERROR: Zendesk base_url is not set. Check ZENDESK_SUBDOMAIN configuration.")
            return []
        
        tickets = []
        # Use the List Tickets endpoint which supports cursor-based pagination
        # and doesn't have the search response size limits
        url = f"{self.base_url}/tickets.json"
        
        params = {
            "page[size]": 100  # Fetch 100 tickets per page (max allowed)
        }
        
        print(f"Fetching tickets from Zendesk using List Tickets API with cursor pagination")
        
        page_count = 0
        retry_count = 0
        max_retries = 3
        
        while url:
            try:
                response = self.session.get(url, params=params)
                
                # Handle rate limiting with exponential backoff
                if response.status_code == 429:
                    retry_after = int(response.headers.get('Retry-After', 60))
                    if retry_count < max_retries:
                        retry_count += 1
                        print(f"Rate limit hit. Waiting {retry_after} seconds before retry {retry_count}/{max_retries}...")
                        time.sleep(retry_after)
                        continue
                    else:
                        error_msg = f"Rate limit exceeded after {max_retries} retries"
                        print(f"ERROR: {error_msg}")
                        raise Exception(error_msg)
                
                response.raise_for_status()
                data = response.json()
                
                # Reset retry count on success
                retry_count = 0
                
                page_tickets = data.get("tickets", [])
                tickets.extend(page_tickets)
                page_count += 1
                print(f"Fetched page {page_count}: {len(page_tickets)} tickets (total: {len(tickets)})")
                
                # Check for next page - Zendesk uses links.next for cursor pagination
                links = data.get("links", {})
                next_page = links.get("next")
                
                if next_page:
                    url = next_page
                    params = None  # next_page URL already includes all params
                    # Minimal delay between requests
                    time.sleep(0.1)
                else:
                    # Also check meta for has_more flag
                    meta = data.get("meta", {})
                    if not meta.get("has_more", False):
                        url = None
                    else:
                        # Shouldn't happen, but fallback to None to avoid infinite loop
                        print("Warning: has_more is true but no next link found")
                        url = None
                        
            except requests.exceptions.HTTPError as e:
                error_msg = f"HTTP Error fetching tickets: {e.response.status_code} - {e.response.text}"
                print(f"ERROR: {error_msg}")
                raise Exception(error_msg)
            except requests.exceptions.RequestException as e:
                error_msg = f"Error fetching tickets: {e}"
                print(f"ERROR: {error_msg}")
                raise Exception(error_msg)
        
        # Filter by status if needed
        if status != "all":
            original_count = len(tickets)
            tickets = [t for t in tickets if t.get("status") == status]
            print(f"Filtered tickets by status '{status}': {len(tickets)} out of {original_count}")
        
        print(f"Total tickets fetched: {len(tickets)}")
        return tickets
    
    def get_ticket_attachments(self, ticket_id: int) -> List[Dict]:
        """
        Get all attachments for a specific ticket with their comment information
        Returns list of attachment dicts with added 'comment_id' field
        """
        if not self.base_url:
            return []
        
        attachments = []
        url = f"{self.base_url}/tickets/{ticket_id}/comments.json"
        
        try:
            response = self.session.get(url)
            response.raise_for_status()
            data = response.json()
            
            # Extract attachments from all comments with comment_id
            for comment in data.get("comments", []):
                comment_id = comment.get("id")
                for attachment in comment.get("attachments", []):
                    # Add comment_id to attachment for later reference
                    attachment_with_comment = attachment.copy()
                    attachment_with_comment["comment_id"] = comment_id
                    attachment_with_comment["is_inline"] = False  # Regular attachment
                    attachments.append(attachment_with_comment)
        except requests.exceptions.RequestException as e:
            print(f"Error fetching attachments for ticket {ticket_id}: {e}")
        
        return attachments
    
    def get_inline_images(self, ticket_id: int) -> List[Dict]:
        """
        Get all inline images from ticket comments
        Returns list of inline image dicts with comment_id and image info
        Inline images are images embedded in comment HTML, not regular attachments.
        These are processed exactly like regular attachments: download, upload to Wasabi, replace with link, delete.
        """
        if not self.base_url:
            return []
        
        inline_images = []
        url = f"{self.base_url}/tickets/{ticket_id}/comments.json"
        
        try:
            response = self.session.get(url)
            response.raise_for_status()
            data = response.json()
            
            print(f"Fetching inline images from {len(data.get('comments', []))} comments for ticket {ticket_id}")
            
            # Extract inline images from all comments
            for comment in data.get("comments", []):
                comment_id = comment.get("id")
                # Prefer html_body for image scanning — body is plain text and may strip img tags
                comment_body = comment.get("html_body") or comment.get("body", "") or ""
                
                if not comment_body:
                    continue
                
                # First, collect all known attachments & inline_attachments for this comment
                # keyed by token so we can match token-URL images to their attachment IDs
                all_comment_atts = comment.get("attachments", []) + comment.get("inline_attachments", [])
                token_to_att = {}
                for att in all_comment_atts:
                    att_url = att.get("content_url", "")
                    token_m = re.search(r'/attachments/token/([^/?]+)', att_url)
                    if token_m:
                        token_to_att[token_m.group(1)] = att
                
                # Find all <img> tags pointing to Zendesk attachment URLs
                img_pattern = r'<img[^>]+src=["\']([^"\']*attachments[^"\']*)["\'][^>]*>'
                matches = list(re.finditer(img_pattern, comment_body, re.IGNORECASE))
                
                if matches:
                    print(f"Found {len(matches)} inline image(s) in comment {comment_id} for ticket {ticket_id}")
                
                for match in matches:
                    img_url = match.group(1)
                    original_html = match.group(0)
                    
                    # Convert relative URLs to absolute
                    if img_url.startswith('/'):
                        img_url = f"https://{self.subdomain}.zendesk.com{img_url}"
                    
                    attachment_id = None
                    filename = "inline_image.png"
                    content_type = "image/png"
                    download_url = img_url
                    
                    # --- Try to match to a known attachment ---
                    img_url_norm = img_url.split('?')[0].rstrip('/')
                    
                    # 1. Token match via pre-built index
                    token_m = re.search(r'/attachments/token/([^/?]+)', img_url)
                    if token_m and token_m.group(1) in token_to_att:
                        att = token_to_att[token_m.group(1)]
                        attachment_id = att.get("id")
                        filename = att.get("file_name", filename)
                        content_type = att.get("content_type", content_type)
                        download_url = att.get("content_url", img_url)
                    
                    if not attachment_id:
                        for att in all_comment_atts:
                            att_url = att.get("content_url", "")
                            att_id = att.get("id")
                            if not att_url or not att_id:
                                continue
                            att_url_norm = att_url.split('?')[0].rstrip('/')
                            
                            # 2. Direct URL match
                            if img_url_norm == att_url_norm or img_url_norm in att_url_norm or att_url_norm in img_url_norm:
                                attachment_id = att_id
                                filename = att.get("file_name", filename)
                                content_type = att.get("content_type", content_type)
                                download_url = att_url
                                break
                            
                            # 3. Numeric ID in URL
                            id_match = re.search(r'/attachments/(\d+)', img_url)
                            if id_match and str(att_id) == id_match.group(1):
                                attachment_id = att_id
                                filename = att.get("file_name", filename)
                                content_type = att.get("content_type", content_type)
                                download_url = att_url
                                break
                            
                            # 4. Filename match
                            fn_match = re.search(r'/([^/?]+\.(?:jpg|jpeg|png|gif|bmp|webp|svg))', img_url, re.IGNORECASE)
                            if fn_match and fn_match.group(1).lower() == att.get("file_name", "").lower():
                                attachment_id = att_id
                                filename = att.get("file_name", filename)
                                content_type = att.get("content_type", content_type)
                                download_url = att_url
                                break
                    
                    # Extract filename from URL if still default
                    if filename == "inline_image.png":
                        name_m = re.search(r'[?&]name=([^&]+)', img_url)
                        if name_m:
                            filename = name_m.group(1)
                        else:
                            fn_m = re.search(r'/([^/?]+\.(?:jpg|jpeg|png|gif|bmp|webp|svg))', img_url, re.IGNORECASE)
                            if fn_m:
                                filename = fn_m.group(1)
                        # Guess content type from extension
                        ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else ''
                        content_type = {
                            'jpg': 'image/jpeg', 'jpeg': 'image/jpeg',
                            'png': 'image/png', 'gif': 'image/gif',
                            'webp': 'image/webp', 'svg': 'image/svg+xml',
                        }.get(ext, 'image/png')
                    
                    # Always include — even token-URL-only images (attachment_id may be None)
                    inline_images.append({
                        "attachment_id": attachment_id,  # None for token-URL-only images
                        "comment_id": comment_id,
                        "content_url": download_url,
                        "file_name": filename,
                        "content_type": content_type,
                        "is_inline": True,
                        "original_html": original_html,
                        "comment_body": comment_body,
                        "img_src": img_url,  # original src for HTML replacement
                    })
                    if attachment_id:
                        print(f"  ✓ Matched inline image '{filename}' to attachment_id={attachment_id}")
                    else:
                        print(f"  ⚠ Token-URL inline image '{filename}' — no attachment_id, will upload+link only")
        except requests.exceptions.RequestException as e:
            print(f"Error fetching inline images for ticket {ticket_id}: {e}")
        
        return inline_images
    
    def get_ticket_comments(self, ticket_id: int) -> List[Dict]:
        """
        Get all comments for a specific ticket
        Returns list of comment dictionaries
        """
        if not self.base_url:
            return []
        
        url = f"{self.base_url}/tickets/{ticket_id}/comments.json"
        
        try:
            response = self.session.get(url)
            response.raise_for_status()
            data = response.json()
            return data.get("comments", [])
        except requests.exceptions.RequestException as e:
            print(f"Error fetching comments for ticket {ticket_id}: {e}")
            return []

    def get_ticket_status(self, ticket_id: int) -> Optional[str]:
        """Return the current status of a ticket (open/pending/solved/closed) or None on error."""
        if not self.base_url:
            return None
        try:
            resp = self.session.get(f"{self.base_url}/tickets/{ticket_id}.json")
            if resp.ok:
                return resp.json().get("ticket", {}).get("status")
        except Exception as e:
            print(f"Error fetching status for ticket {ticket_id}: {e}")
        return None

    def add_wasabi_link_comment(self, ticket_id: int, comment_id: int, wasabi_url: str, filename: str, img_src: str) -> bool:
        """
        For token-URL-only inline images (no attachment_id): add a private comment with
        a Wasabi link so the file is reachable after the original Zendesk-hosted URL expires.
        Cannot redact — just adds a reference link.
        """
        if not self.base_url:
            return False
        try:
            body = (
                f'<p>📎 Image backed up to Wasabi: '
                f'<a href="{wasabi_url}" target="_blank" rel="noopener noreferrer">{filename}</a></p>'
                f'<p><small>Original src: {img_src}</small></p>'
            )
            update_data = {"ticket": {"comment": {"html_body": body, "public": False}}}
            resp = self.session.put(f"{self.base_url}/tickets/{ticket_id}.json", json=update_data)
            resp.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            print(f"Error adding Wasabi link comment for ticket {ticket_id}: {e}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"  Response: {e.response.text[:300]}")
            return False

    def replace_inline_image_in_comment(self, ticket_id: int, comment_id: int, attachment_id: int, wasabi_url: str, filename: str, original_html: str) -> bool:
        """
        Replace an inline image in a comment with a Wasabi link
        Since Zendesk API doesn't allow updating existing comments directly,
        we'll replace the image in the comment body and add a new comment with the modified body,
        then delete the inline image from the original comment
        """
        if not self.base_url:
            return False
        
        try:
            # Get the original comment to get its full body and visibility
            comments = self.get_ticket_comments(ticket_id)
            original_comment = None
            for comment in comments:
                if comment.get("id") == comment_id:
                    original_comment = comment
                    break
            
            if not original_comment:
                print(f"Comment {comment_id} not found for ticket {ticket_id}")
                return False
            
            comment_body = original_comment.get("body", "")
            is_public = original_comment.get("public", True)
            
            # Replace the inline image HTML with a Wasabi link
            # Create a link in markdown format: [filename](wasabi_url)
            wasabi_link = f'<a href="{wasabi_url}" target="_blank" rel="noopener noreferrer">{filename}</a>'
            
            # Replace the original <img> tag with the Wasabi link
            # Use re.escape to handle special characters in original_html
            import re
            escaped_html = re.escape(original_html)
            modified_body = re.sub(escaped_html, wasabi_link, comment_body, flags=re.IGNORECASE)
            
            # If the replacement didn't work (maybe HTML was slightly different), try a more flexible approach
            if modified_body == comment_body:
                # Try to find and replace just the img tag more flexibly
                # Pattern: <img ... src="..." ... />
                img_pattern = r'<img[^>]*src=["\']' + re.escape(original_html.split('src="')[1].split('"')[0] if 'src="' in original_html else '') + r'["\'][^>]*>'
                modified_body = re.sub(img_pattern, wasabi_link, comment_body, flags=re.IGNORECASE)
            
            # If still no change, try replacing just the src URL
            if modified_body == comment_body:
                # Extract the src URL from original_html
                src_match = re.search(r'src=["\']([^"\']*)["\']', original_html, re.IGNORECASE)
                if src_match:
                    src_url = src_match.group(1)
                    # Replace any img tag with this src
                    img_pattern = r'<img[^>]*src=["\']' + re.escape(src_url) + r'["\'][^>]*>'
                    modified_body = re.sub(img_pattern, wasabi_link, comment_body, flags=re.IGNORECASE)
            
            # If we successfully modified the body, add a new comment with the modified content
            if modified_body != comment_body:
                url = f"{self.base_url}/tickets/{ticket_id}.json"
                
                # Add a new comment with the modified body (replacing inline image with link)
                update_data = {
                    "ticket": {
                        "comment": {
                            "body": modified_body,
                            "public": is_public
                        }
                    }
                }
                
                response = self.session.put(url, json=update_data)
                response.raise_for_status()
                print(f"Added new comment with Wasabi link replacing inline image in comment {comment_id}")
            else:
                # Fallback: just add a simple link comment
                url = f"{self.base_url}/tickets/{ticket_id}.json"
                wasabi_link_text = f'<p><a href="{wasabi_url}" target="_blank" rel="noopener noreferrer">Image: {filename}</a></p>'
                
                update_data = {
                    "ticket": {
                        "comment": {
                            "body": wasabi_link_text,
                            "public": is_public
                        }
                    }
                }
                
                response = self.session.put(url, json=update_data)
                response.raise_for_status()
                print(f"Added new comment with Wasabi link for inline image (could not modify original comment)")
            
            # Now redact (delete) the inline image attachment from the original comment
            print(f"Attempting to delete inline image attachment {attachment_id} from comment {comment_id} in ticket {ticket_id}")
            delete_success = self.delete_attachment(ticket_id, comment_id, attachment_id)
            
            if delete_success:
                print(f"✓ Successfully deleted inline image attachment {attachment_id} from Zendesk")
            else:
                print(f"✗ Failed to delete inline image attachment {attachment_id} from Zendesk")
            
            return delete_success
            
        except requests.exceptions.RequestException as e:
            error_msg = f"Error replacing inline image {attachment_id} in comment {comment_id} for ticket {ticket_id}: {e}"
            print(f"✗ {error_msg}")
            if hasattr(e, 'response') and e.response is not None:
                print(f"   Response status: {e.response.status_code}")
                print(f"   Response text: {e.response.text[:500]}")
            return False
        except Exception as e:
            error_msg = f"Unexpected error replacing inline image {attachment_id} in comment {comment_id} for ticket {ticket_id}: {e}"
            print(f"✗ {error_msg}")
            import traceback
            traceback.print_exc()
            return False
    
    def replace_attachment_in_comment(self, ticket_id: int, comment_id: int, attachment_id: int, wasabi_url: str, filename: str) -> bool:
        """
        Replace an attachment in a comment with a Wasabi link
        Since Zendesk API doesn't allow updating existing comments directly,
        we'll add a new comment with the Wasabi link and then delete the attachment
        """
        if not self.base_url:
            return False
        
        url = f"{self.base_url}/tickets/{ticket_id}.json"
        
        try:
            # Check ticket status — closed tickets cannot be updated via Zendesk API
            ticket_resp = self.session.get(url)
            ticket_status = None
            if ticket_resp.ok:
                ticket_status = ticket_resp.json().get("ticket", {}).get("status")

            # Get the original comment to check if it's public or private
            comments = self.get_ticket_comments(ticket_id)
            original_comment = None
            for comment in comments:
                if comment.get("id") == comment_id:
                    original_comment = comment
                    break
            
            is_public = True
            if original_comment:
                is_public = original_comment.get("public", True)

            if ticket_status == "closed":
                # Closed tickets cannot receive new comments or redactions — skip silently,
                # the attachment has already been uploaded to Wasabi.
                logger.info(f"Ticket {ticket_id} is closed — skipping Zendesk comment and redact (Zendesk blocks updates on closed tickets)")
                return True  # treat as success — file is safely in Wasabi
            else:
                # Create a new comment with the Wasabi link
                # Format: [Attachment Secured: filename (link)]
                wasabi_link_text = f"[Attachment Secured: {filename}]({wasabi_url})"
                
                update_data = {
                    "ticket": {
                        "comment": {
                            "body": wasabi_link_text,
                            "public": is_public
                        }
                    }
                }
                
                response = self.session.put(url, json=update_data)
                response.raise_for_status()
                
                # Now redact (delete) the attachment from the original comment
                return self.delete_attachment(ticket_id, comment_id, attachment_id)
            
        except requests.exceptions.RequestException as e:
            print(f"Error replacing attachment {attachment_id} in comment {comment_id} for ticket {ticket_id}: {e}")
            return False
    
    def delete_attachment(self, ticket_id: int, comment_id: int, attachment_id: int) -> bool:
        """
        Delete (redact) an attachment from a Zendesk comment
        Uses Zendesk's Redact API which replaces the attachment with a redacted.txt file
        """
        if not self.base_url:
            print(f"ERROR: Cannot delete attachment - base_url not set")
            return False
        
        # Zendesk Redact API endpoint
        url = f"{self.base_url}/tickets/{ticket_id}/comments/{comment_id}/attachments/{attachment_id}/redact.json"
        
        print(f"Redacting attachment {attachment_id} from comment {comment_id} in ticket {ticket_id}")
        print(f"  URL: {url}")
        
        try:
            # Redact API requires PUT request with empty body
            response = self.session.put(url, json={})
            response.raise_for_status()
            print(f"✓ Successfully redacted attachment {attachment_id}")
            return True
        except requests.exceptions.HTTPError as e:
            # Check if it's a 404 (attachment might already be deleted) or other error
            if e.response.status_code == 404:
                print(f"Attachment {attachment_id} not found (may already be deleted) - considering success")
                return True  # Consider it successful if already gone
            elif e.response.status_code == 403:
                error_msg = f"Permission denied (403) when redacting attachment {attachment_id}. Check API permissions."
                print(f"ERROR: {error_msg}")
                print(f"   Response: {e.response.text[:500]}")
                return False
            else:
                error_msg = f"HTTP Error {e.response.status_code} redacting attachment {attachment_id}: {e.response.text[:500]}"
                print(f"ERROR: {error_msg}")
                return False
        except requests.exceptions.RequestException as e:
            print(f"ERROR: Request exception when redacting attachment {attachment_id}: {e}")
            return False
    
    def download_attachment(self, attachment_url: str) -> Optional[bytes]:
        """
        Download attachment content
        Handles both regular attachment URLs and inline image URLs
        """
        try:
            # Ensure URL is absolute
            if attachment_url.startswith('/'):
                attachment_url = f"https://{self.subdomain}.zendesk.com{attachment_url}"
            
            # Use the session which has authentication
            response = self.session.get(attachment_url, timeout=30)
            response.raise_for_status()
            
            # Check if we got actual content
            if response.content:
                return response.content
            else:
                print(f"Warning: Empty content downloaded from {attachment_url}")
                return None
        except requests.exceptions.HTTPError as e:
            print(f"HTTP Error downloading attachment from {attachment_url}: {e.response.status_code} - {e.response.text[:200]}")
            return None
        except requests.exceptions.RequestException as e:
            print(f"Error downloading attachment from {attachment_url}: {e}")
            return None
    
    def mark_ticket_as_read(self, ticket_id: int) -> bool:
        """
        Mark ticket as read by updating it
        Note: Zendesk doesn't have a direct "mark as read" API,
        but we can update the ticket status or add a tag
        """
        if not self.base_url:
            return False
        
        url = f"{self.base_url}/tickets/{ticket_id}.json"
        
        try:
            # Get current ticket
            response = self.session.get(url)
            response.raise_for_status()
            ticket = response.json().get("ticket", {})
            
            # Update ticket with a tag to mark as processed
            # This is a workaround since Zendesk doesn't have read/unread status
            current_tags = ticket.get("tags", [])
            if "processed_by_offloader" not in current_tags:
                current_tags.append("processed_by_offloader")
            
            update_data = {
                "ticket": {
                    "tags": current_tags
                }
            }
            
            response = self.session.put(url, json=update_data)
            response.raise_for_status()
            return True
        except requests.exceptions.RequestException as e:
            print(f"Error marking ticket {ticket_id} as read: {e}")
            return False
    
    def get_recently_updated_tickets(self, since_minutes: int = 10) -> List[Dict]:
        """
        Fetch only tickets updated in the last `since_minutes` minutes using the
        Zendesk incremental tickets API (/incremental/tickets/cursor).
        Much faster than a full scan — ideal for continuous/interval offload.
        Returns list of ticket dicts (may include deleted/spam; caller should filter).
        """
        if not self.base_url:
            return []

        from datetime import datetime, timezone, timedelta
        import math

        since_dt = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
        since_ts = math.floor(since_dt.timestamp())

        tickets = []
        url = f"{self.base_url}/incremental/tickets.json"
        params = {"start_time": since_ts}

        logger.info(f"Fetching tickets updated since {since_dt.strftime('%Y-%m-%d %H:%M:%S')} UTC (last {since_minutes} min)")

        while url:
            try:
                response = self.session.get(url, params=params)
                if response.status_code == 429:
                    retry_after = int(response.headers.get('Retry-After', 30))
                    logger.warning(f"Rate limited — waiting {retry_after}s")
                    time.sleep(retry_after)
                    continue
                if response.status_code == 422:
                    # start_time too recent (Zendesk requires at least 1 min ago) — return empty
                    logger.info("Incremental API: start_time too recent, no tickets yet")
                    return []
                response.raise_for_status()
                data = response.json()

                page_tickets = data.get("tickets", [])
                # Filter out deleted/spam tickets
                active = [t for t in page_tickets if t.get("status") not in ("deleted",)]
                tickets.extend(active)

                # Incremental API uses end_of_stream flag
                if data.get("end_of_stream", True):
                    break
                next_url = data.get("next_page")
                if not next_url:
                    break
                url = next_url
                params = None

            except Exception as e:
                logger.error(f"Error fetching recent tickets: {e}")
                break

        logger.info(f"Incremental fetch returned {len(tickets)} active tickets updated in last {since_minutes} min")
        return tickets

    def get_new_tickets(self, processed_ticket_ids: set) -> List[Dict]:
        """
        Get only new tickets that haven't been processed
        Fetches ALL tickets to find new ones (uses cursor pagination, no 10K limit)
        """
        print(f"Getting new tickets. Already processed: {len(processed_ticket_ids)} tickets")
        
        if not self.base_url:
            print("ERROR: Zendesk base_url is not set.")
            return []
        
        max_processed_id = max(processed_ticket_ids) if processed_ticket_ids else 0
        print(f"Max processed ticket ID: {max_processed_id}")
        
        # Fetch ALL tickets using cursor pagination
        # This is necessary because new tickets could be anywhere in the list
        all_tickets = []
        url = f"{self.base_url}/tickets.json"
        params = {"page[size]": 100}
        
        page_count = 0
        
        print(f"Fetching all tickets to find new ones...")
        
        while url:
            try:
                response = self.session.get(url, params=params)
                response.raise_for_status()
                data = response.json()
                
                page_tickets = data.get("tickets", [])
                all_tickets.extend(page_tickets)
                page_count += 1
                
                # Show progress every 20 pages
                if page_count % 20 == 0:
                    print(f"Fetched {page_count} pages, {len(all_tickets)} tickets so far...")
                
                # Check for next page
                links = data.get("links", {})
                next_page = links.get("next")
                
                if next_page:
                    url = next_page
                    params = None
                    time.sleep(0.1)
                else:
                    break
                    
            except requests.exceptions.RequestException as e:
                print(f"Error fetching tickets: {e}")
                logger.error(f"Error fetching tickets: {e}")
                break
        
        print(f"Fetched total of {len(all_tickets)} tickets in {page_count} pages")
        
        # Filter to only new tickets
        new_tickets = [
            ticket for ticket in all_tickets 
            if ticket.get("id") not in processed_ticket_ids
        ]
        
        # Sort by ID descending (newest first)
        new_tickets.sort(key=lambda x: x.get("id", 0), reverse=True)
        
        print(f"Found {len(new_tickets)} new tickets")
        if new_tickets:
            ids = [t.get("id") for t in new_tickets[:5]]
            print(f"New ticket IDs: {ids}")
        
        return new_tickets


