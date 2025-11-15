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
        Get all tickets from Zendesk
        Returns list of ticket dictionaries
        """
        if not self.base_url:
            print("ERROR: Zendesk base_url is not set. Check ZENDESK_SUBDOMAIN configuration.")
            return []
        
        tickets = []
        url = f"{self.base_url}/tickets.json"
        params = {"status": status}
        
        print(f"Fetching tickets from Zendesk: {url}")
        
        while url:
            try:
                response = self.session.get(url, params=params)
                response.raise_for_status()
                data = response.json()
                page_tickets = data.get("tickets", [])
                tickets.extend(page_tickets)
                print(f"Fetched {len(page_tickets)} tickets (total: {len(tickets)})")
                
                # Check for next page
                url = data.get("next_page")
                params = None  # next_page already includes params
            except requests.exceptions.HTTPError as e:
                error_msg = f"HTTP Error fetching tickets: {e.response.status_code} - {e.response.text}"
                print(f"ERROR: {error_msg}")
                raise Exception(error_msg)
            except requests.exceptions.RequestException as e:
                error_msg = f"Error fetching tickets: {e}"
                print(f"ERROR: {error_msg}")
                raise Exception(error_msg)
        
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
        Inline images are images embedded in comment HTML, not regular attachments
        """
        if not self.base_url:
            return []
        
        inline_images = []
        url = f"{self.base_url}/tickets/{ticket_id}/comments.json"
        
        try:
            response = self.session.get(url)
            response.raise_for_status()
            data = response.json()
            
            # Extract inline images from all comments
            for comment in data.get("comments", []):
                comment_id = comment.get("id")
                comment_body = comment.get("body", "")
                
                if not comment_body:
                    continue
                
                # Find all inline images in HTML
                # Pattern: <img src="https://subdomain.zendesk.com/attachments/..." />
                # or <img src="/attachments/..." />
                # Also handle data URLs and other formats
                img_pattern = r'<img[^>]+src=["\']([^"\']*attachments[^"\']*)["\'][^>]*>'
                matches = re.finditer(img_pattern, comment_body, re.IGNORECASE)
                
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
                    
                    # Try to find the attachment in the comment's attachments list
                    # Match by URL pattern or attachment ID
                    for att in comment.get("attachments", []):
                        att_url = att.get("content_url", "")
                        att_id = att.get("id")
                        
                        # Check if this attachment matches the inline image URL
                        if img_url in att_url or att_url in img_url:
                            attachment_id = att_id
                            filename = att.get("file_name", "inline_image.png")
                            content_type = att.get("content_type", "image/png")
                            break
                        
                        # Also try matching by extracting ID from URL
                        if '/attachments/' in img_url:
                            # Try to extract numeric ID from URL
                            id_match = re.search(r'/attachments/(\d+)', img_url)
                            if id_match and str(att_id) == id_match.group(1):
                                attachment_id = att_id
                                filename = att.get("file_name", "inline_image.png")
                                content_type = att.get("content_type", "image/png")
                                break
                    
                    # If we found an attachment ID, add it to the list
                    if attachment_id:
                        inline_images.append({
                            "attachment_id": attachment_id,
                            "comment_id": comment_id,
                            "content_url": img_url,
                            "file_name": filename,
                            "content_type": content_type,
                            "is_inline": True,
                            "original_html": original_html,
                            "comment_body": comment_body
                        })
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
        we'll add a new comment with the Wasabi link and then delete the inline image
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
            # Format: [Image Secured: filename (link)]
            wasabi_link_text = f"[Image Secured: {filename}]({wasabi_url})"
            
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
            
            # Now redact (delete) the inline image attachment
            return self.delete_attachment(ticket_id, comment_id, attachment_id)
            
        except requests.exceptions.RequestException as e:
            print(f"Error replacing inline image {attachment_id} in comment {comment_id} for ticket {ticket_id}: {e}")
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
            return False
        
        # Zendesk Redact API endpoint
        url = f"{self.base_url}/tickets/{ticket_id}/comments/{comment_id}/attachments/{attachment_id}/redact.json"
        
        try:
            # Redact API requires PUT request with empty body
            response = self.session.put(url, json={})
            response.raise_for_status()
            return True
        except requests.exceptions.HTTPError as e:
            # Check if it's a 404 (attachment might already be deleted) or other error
            if e.response.status_code == 404:
                print(f"Attachment {attachment_id} not found (may already be deleted)")
                return True  # Consider it successful if already gone
            error_msg = f"HTTP Error redacting attachment {attachment_id}: {e.response.status_code} - {e.response.text}"
            print(f"ERROR: {error_msg}")
            return False
        except requests.exceptions.RequestException as e:
            print(f"Error redacting attachment {attachment_id}: {e}")
            return False
    
    def download_attachment(self, attachment_url: str) -> Optional[bytes]:
        """
        Download attachment content
        """
        try:
            response = self.session.get(attachment_url)
            response.raise_for_status()
            return response.content
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


