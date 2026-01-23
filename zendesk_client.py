"""
Zendesk API client for fetching tickets and attachments
"""
import requests
import base64
import re
from typing import List, Dict, Optional
from config import ZENDESK_SUBDOMAIN, ZENDESK_EMAIL, ZENDESK_API_TOKEN

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
        Get all tickets from Zendesk using cursor-based pagination
        Returns list of ticket dictionaries
        """
        if not self.base_url:
            print("ERROR: Zendesk base_url is not set. Check ZENDESK_SUBDOMAIN configuration.")
            return []
        
        tickets = []
        # Use incremental export API with cursor-based pagination
        # This endpoint is designed for fetching large numbers of tickets
        url = f"{self.base_url}/incremental/tickets/cursor.json"
        params = {"start_time": "0"}  # Start from the beginning
        
        print(f"Fetching tickets from Zendesk using cursor-based pagination: {url}")
        
        has_more = True
        page_count = 0
        
        while has_more:
            try:
                response = self.session.get(url, params=params)
                response.raise_for_status()
                data = response.json()
                
                page_tickets = data.get("tickets", [])
                tickets.extend(page_tickets)
                page_count += 1
                print(f"Fetched page {page_count}: {len(page_tickets)} tickets (total: {len(tickets)})")
                
                # Check for more pages using cursor pagination
                has_more = not data.get("end_of_stream", False)
                
                if has_more:
                    # Get the after_cursor for next page
                    after_cursor = data.get("after_cursor")
                    if after_cursor:
                        # Update URL and params for next request
                        url = f"{self.base_url}/incremental/tickets/cursor.json"
                        params = {"cursor": after_cursor}
                    else:
                        # No cursor provided, stop pagination
                        has_more = False
                        print("No after_cursor provided, ending pagination")
                        
            except requests.exceptions.HTTPError as e:
                error_msg = f"HTTP Error fetching tickets: {e.response.status_code} - {e.response.text}"
                print(f"ERROR: {error_msg}")
                raise Exception(error_msg)
            except requests.exceptions.RequestException as e:
                error_msg = f"Error fetching tickets: {e}"
                print(f"ERROR: {error_msg}")
                raise Exception(error_msg)
        
        # Filter by status if needed (incremental API returns all tickets)
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
                comment_body = comment.get("body", "")
                
                if not comment_body:
                    continue
                
                # Find all inline images in HTML
                # Pattern: <img src="https://subdomain.zendesk.com/attachments/..." />
                # or <img src="/attachments/..." />
                # Match img tags with attachments in src, including various formats
                img_pattern = r'<img[^>]+src=["\']([^"\']*attachments[^"\']*)["\'][^>]*>'
                matches = list(re.finditer(img_pattern, comment_body, re.IGNORECASE))
                
                # If no matches with attachments pattern, try a more general pattern
                if not matches:
                    img_pattern = r'<img[^>]+src=["\']([^"\']*zendesk[^"\']*attachments[^"\']*)["\'][^>]*>'
                    matches = list(re.finditer(img_pattern, comment_body, re.IGNORECASE))
                
                if matches:
                    print(f"Found {len(matches)} inline image(s) in comment {comment_id} for ticket {ticket_id}")
                
                for match in matches:
                    img_url = match.group(1)
                    original_html = match.group(0)
                    
                    # Convert relative URLs to absolute
                    if img_url.startswith('/'):
                        img_url = f"https://{self.subdomain}.zendesk.com{img_url}"
                    
                    # Extract attachment token/ID from URL
                    # URL format: https://subdomain.zendesk.com/attachments/token/TOKEN/filename
                    # or https://subdomain.zendesk.com/attachments/attachment_id
                    attachment_id = None
                    filename = "inline_image.png"
                    content_type = "image/png"
                    attachment_content_url = None
                    
                    # Try to find the attachment in the comment's attachments list
                    # Match by URL pattern or attachment ID
                    for att in comment.get("attachments", []):
                        att_url = att.get("content_url", "")
                        att_id = att.get("id")
                        
                        if not att_url or not att_id:
                            continue
                        
                        # Check if this attachment matches the inline image URL
                        # Normalize URLs for comparison (remove query params, etc.)
                        img_url_normalized = img_url.split('?')[0].rstrip('/')
                        att_url_normalized = att_url.split('?')[0].rstrip('/')
                        
                        # Direct match
                        if img_url_normalized == att_url_normalized:
                            attachment_id = att_id
                            filename = att.get("file_name", "inline_image.png")
                            content_type = att.get("content_type", "image/png")
                            attachment_content_url = att_url  # Use the API's content_url for downloading
                            break
                        
                        # Substring match (one URL contains the other)
                        if img_url_normalized in att_url_normalized or att_url_normalized in img_url_normalized:
                            attachment_id = att_id
                            filename = att.get("file_name", "inline_image.png")
                            content_type = att.get("content_type", "image/png")
                            attachment_content_url = att_url
                            break
                        
                        # Try matching by extracting ID from URL
                        # URL format: /attachments/12345 or /attachments/token/TOKEN/filename
                        if '/attachments/' in img_url:
                            # Try to extract numeric ID from URL
                            id_match = re.search(r'/attachments/(\d+)', img_url)
                            if id_match and str(att_id) == id_match.group(1):
                                attachment_id = att_id
                                filename = att.get("file_name", "inline_image.png")
                                content_type = att.get("content_type", "image/png")
                                attachment_content_url = att_url
                                break
                            
                            # Try matching by token in URL
                            # URL format: /attachments/token/TOKEN/filename
                            token_match = re.search(r'/attachments/token/([^/]+)', img_url)
                            if token_match:
                                token = token_match.group(1)
                                # Check if attachment URL contains this token
                                if token in att_url:
                                    attachment_id = att_id
                                    filename = att.get("file_name", "inline_image.png")
                                    content_type = att.get("content_type", "image/png")
                                    attachment_content_url = att_url
                                    break
                        
                        # Try matching by filename in URL
                        # Extract filename from img_url and compare with attachment filename
                        filename_match = re.search(r'/([^/]+\.(jpg|jpeg|png|gif|bmp|webp|svg))', img_url, re.IGNORECASE)
                        if filename_match:
                            url_filename = filename_match.group(1)
                            att_filename = att.get("file_name", "")
                            if url_filename.lower() == att_filename.lower():
                                attachment_id = att_id
                                filename = att_filename
                                content_type = att.get("content_type", "image/png")
                                attachment_content_url = att_url
                                break
                    
                    # If we found an attachment ID, add it to the list
                    if attachment_id:
                        # Use the attachment's content_url from API for downloading, not the img src URL
                        download_url = attachment_content_url if attachment_content_url else img_url
                        inline_images.append({
                            "attachment_id": attachment_id,
                            "comment_id": comment_id,
                            "content_url": download_url,  # Use API content_url for reliable downloading
                            "file_name": filename,
                            "content_type": content_type,
                            "is_inline": True,
                            "original_html": original_html,
                            "comment_body": comment_body
                        })
                    else:
                        # Log when we find an inline image but can't match it to an attachment
                        print(f"  ✗ Warning: Found inline image in ticket {ticket_id}, comment {comment_id}, but could not match to attachment.")
                        print(f"    Image URL: {img_url}")
                        print(f"    Available attachments in comment: {[att.get('id') for att in comment.get('attachments', [])]}")
                        print(f"    Attachment URLs: {[att.get('content_url', '')[:80] for att in comment.get('attachments', [])]}")
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
            
            # Create a new comment with the Wasabi link
            # Format: [Attachment Secured: filename (link)]
            wasabi_link_text = f"[Attachment Secured: {filename}]({wasabi_url})"
            
            # Add a new comment with the Wasabi link
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
    
    def get_new_tickets(self, processed_ticket_ids: set) -> List[Dict]:
        """
        Get only new tickets that haven't been processed
        """
        print(f"Getting new tickets. Already processed: {len(processed_ticket_ids)} tickets")
        all_tickets = self.get_all_tickets()
        new_tickets = [
            ticket for ticket in all_tickets 
            if ticket.get("id") not in processed_ticket_ids
        ]
        print(f"Found {len(new_tickets)} new tickets out of {len(all_tickets)} total")
        return new_tickets


