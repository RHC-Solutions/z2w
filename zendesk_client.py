"""
Zendesk API client for fetching tickets and attachments
"""
import requests
import base64
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
        Get all attachments for a specific ticket
        """
        if not self.base_url:
            return []
        
        attachments = []
        url = f"{self.base_url}/tickets/{ticket_id}/comments.json"
        
        try:
            response = self.session.get(url)
            response.raise_for_status()
            data = response.json()
            
            # Extract attachments from all comments
            for comment in data.get("comments", []):
                attachments.extend(comment.get("attachments", []))
        except requests.exceptions.RequestException as e:
            print(f"Error fetching attachments for ticket {ticket_id}: {e}")
        
        return attachments
    
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


