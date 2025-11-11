"""
Wasabi B2 (S3-compatible) client for uploading attachments
"""
import boto3
from botocore.exceptions import ClientError
from datetime import datetime
from typing import Optional
from config import WASABI_ENDPOINT, WASABI_ACCESS_KEY, WASABI_SECRET_KEY, WASABI_BUCKET_NAME

class WasabiClient:
    """Client for interacting with Wasabi B2 storage"""
    
    def __init__(self, endpoint=None, access_key=None, secret_key=None, bucket_name=None):
        # Allow overriding credentials for testing
        self.endpoint = endpoint or WASABI_ENDPOINT
        self.access_key = access_key or WASABI_ACCESS_KEY
        self.secret_key = secret_key or WASABI_SECRET_KEY
        self.bucket_name = bucket_name or WASABI_BUCKET_NAME
        self._s3_client = None
    
    def _get_s3_client(self):
        """Lazy initialization of S3 client"""
        if self._s3_client is None:
            if not self.endpoint or not self.access_key or not self.secret_key:
                raise ValueError("Wasabi credentials not configured. Please set WASABI_ENDPOINT, WASABI_ACCESS_KEY, and WASABI_SECRET_KEY in .env file")
            
            # Normalize endpoint URL
            endpoint = self.endpoint.strip()
            if endpoint and not endpoint.startswith('http'):
                endpoint = f"https://{endpoint}"
            
            # Initialize S3 client (Wasabi is S3-compatible)
            self._s3_client = boto3.client(
                's3',
                endpoint_url=endpoint,
                aws_access_key_id=self.access_key.strip(),
                aws_secret_access_key=self.secret_key.strip()
            )
        return self._s3_client
    
    @property
    def s3_client(self):
        """Property to access S3 client with lazy initialization"""
        return self._get_s3_client()
    
    def upload_attachment(
        self, 
        ticket_id: int, 
        attachment_data: bytes, 
        original_filename: str,
        content_type: str = "application/octet-stream"
    ) -> Optional[str]:
        """
        Upload attachment to Wasabi B2
        Returns the S3 key if successful, None otherwise
        
        Format: YYYYMMDD/ticketID_original_filename
        """
        # Create date-based folder (YYYYMMDD)
        date_folder = datetime.utcnow().strftime("%Y%m%d")
        
        # Ensure filename starts with ticketID_
        if not original_filename.startswith(f"{ticket_id}_"):
            filename = f"{ticket_id}_{original_filename}"
        else:
            filename = original_filename
        
        # Create S3 key
        s3_key = f"{date_folder}/{filename}"
        
        try:
            # Upload to Wasabi
            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=s3_key,
                Body=attachment_data,
                ContentType=content_type
            )
            return s3_key
        except (ClientError, ValueError) as e:
            print(f"Error uploading {filename} to Wasabi: {e}")
            return None
    
    def test_connection(self) -> tuple[bool, str]:
        """Test connection to Wasabi B2
        Returns (success: bool, message: str)
        """
        try:
            if not self.endpoint:
                return False, "WASABI_ENDPOINT is not set"
            if not self.access_key:
                return False, "WASABI_ACCESS_KEY is not set"
            if not self.secret_key:
                return False, "WASABI_SECRET_KEY is not set"
            if not self.bucket_name:
                return False, "WASABI_BUCKET_NAME is not set"
            
            # Reset client to ensure fresh connection
            self._s3_client = None
            
            # Test connection
            client = self._get_s3_client()
            client.head_bucket(Bucket=self.bucket_name)
            return True, "Successfully connected to Wasabi B2!"
        except ValueError as e:
            return False, str(e)
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_msg = e.response.get('Error', {}).get('Message', str(e))
            if error_code == '403':
                return False, f"Access denied. Check your credentials. Error: {error_msg}"
            elif error_code == '404':
                return False, f"Bucket '{self.bucket_name}' not found. Error: {error_msg}"
            else:
                return False, f"Connection failed: {error_code} - {error_msg}"
        except Exception as e:
            return False, f"Unexpected error: {str(e)}"


