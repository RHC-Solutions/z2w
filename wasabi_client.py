"""
Wasabi B2 (S3-compatible) client for uploading attachments
"""
import boto3
from botocore.exceptions import ClientError
from datetime import datetime
from typing import Optional
from config import WASABI_ENDPOINT, WASABI_ACCESS_KEY, WASABI_SECRET_KEY, WASABI_BUCKET_NAME

def _human_size(n: int) -> str:
    """Return a human-readable file size string."""
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if n < 1024:
            return f'{n:.1f} {unit}' if unit != 'B' else f'{n} B'
        n /= 1024
    return f'{n:.1f} PB'


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
        
        Key format: YYYYMMDD/ticketID_YYYYMMDD_original_filename
        """
        # Create date-based folder (YYYYMMDD)
        date_folder = datetime.utcnow().strftime("%Y%m%d")
        date_str = date_folder
        
        # Ensure filename format ticketID_YYYYMMDD_original_filename
        prefix_ticket = f"{ticket_id}_"
        prefix_full = f"{ticket_id}_{date_str}_"
        if original_filename.startswith(prefix_full):
            filename = original_filename
        elif original_filename.startswith(prefix_ticket):
            # Insert date after the ticket id
            remainder = original_filename[len(prefix_ticket):]
            filename = f"{ticket_id}_{date_str}_{remainder}"
        else:
            filename = f"{ticket_id}_{date_str}_{original_filename}"
        
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
    
    def get_file_url(self, s3_key: str, expires_in: int = 3600) -> Optional[str]:
        """
        Generate a presigned URL for accessing a file in Wasabi
        Returns the URL if successful, None otherwise
        
        Args:
            s3_key: The S3 key (path) of the file
            expires_in: URL expiration time in seconds (default: 1 hour)
        """
        try:
            if not self.endpoint or not self.bucket_name:
                return None
            
            # Normalize endpoint URL
            endpoint = self.endpoint.strip()
            if endpoint and not endpoint.startswith('http'):
                endpoint = f"https://{endpoint}"
            
            # Generate presigned URL
            client = self._get_s3_client()
            url = client.generate_presigned_url(
                'get_object',
                Params={'Bucket': self.bucket_name, 'Key': s3_key},
                ExpiresIn=expires_in
            )
            return url
        except Exception as e:
            print(f"Error generating URL for {s3_key}: {e}")
            return None
    
    def get_public_url(self, s3_key: str) -> Optional[str]:
        """
        Generate a public URL for accessing a file in Wasabi (if bucket is public)
        Returns the URL if successful, None otherwise
        
        Args:
            s3_key: The S3 key (path) of the file
        """
        try:
            if not self.endpoint or not self.bucket_name:
                return None
            
            # Normalize endpoint URL
            endpoint = self.endpoint.strip()
            if endpoint and not endpoint.startswith('http'):
                endpoint = f"https://{endpoint}"
            
            # Remove trailing slash from endpoint if present
            endpoint = endpoint.rstrip('/')
            
            # Construct public URL
            # Format: https://endpoint/bucket/key
            url = f"{endpoint}/{self.bucket_name}/{s3_key}"
            return url
        except Exception as e:
            print(f"Error generating public URL for {s3_key}: {e}")
            return None
    
    def get_storage_stats(self) -> dict:
        """
        Return bucket storage statistics (total objects, total size).
        Uses list_objects_v2 with pagination — may take a few seconds for large buckets.
        Returns dict with keys: object_count, total_bytes, total_mb, total_gb, error
        """
        stats = {"object_count": 0, "total_bytes": 0, "total_mb": 0.0, "total_gb": 0.0, "error": None}
        try:
            client = self._get_s3_client()
            paginator = client.get_paginator('list_objects_v2')
            pages = paginator.paginate(Bucket=self.bucket_name)
            for page in pages:
                for obj in page.get('Contents', []):
                    stats["object_count"] += 1
                    stats["total_bytes"] += obj.get('Size', 0)
            stats["total_mb"] = stats["total_bytes"] / (1024 * 1024)
            stats["total_gb"] = stats["total_bytes"] / (1024 * 1024 * 1024)
        except Exception as e:
            stats["error"] = str(e)
        return stats

    def list_objects(self, prefix: str = '', delimiter: str = '/') -> dict:
        """
        List objects (files) and common prefixes (folders) at the given prefix.

        Returns:
            {
              'folders': [{'prefix': str, 'name': str}, ...],
              'files':   [{'key': str, 'name': str, 'size': int,
                           'size_human': str, 'last_modified': datetime}, ...],
              'error': str | None
            }
        """
        result = {'folders': [], 'files': [], 'error': None}
        try:
            client = self._get_s3_client()
            paginator = client.get_paginator('list_objects_v2')
            pages = paginator.paginate(
                Bucket=self.bucket_name,
                Prefix=prefix,
                Delimiter=delimiter,
            )
            for page in pages:
                for cp in page.get('CommonPrefixes') or []:
                    p = cp['Prefix']
                    name = p.rstrip('/').split('/')[-1]
                    result['folders'].append({'prefix': p, 'name': name})
                for obj in page.get('Contents') or []:
                    key = obj['Key']
                    if key == prefix:          # skip the "folder" placeholder itself
                        continue
                    name = key.split('/')[-1]
                    size = obj.get('Size', 0)
                    result['files'].append({
                        'key': key,
                        'name': name,
                        'size': size,
                        'size_human': _human_size(size),
                        'last_modified': obj.get('LastModified'),
                    })
        except Exception as e:
            result['error'] = str(e)
        return result

    def presign_url(self, key: str, expires_in: int = 3600) -> str:
        """Return a presigned GET URL for *key*, valid for *expires_in* seconds."""
        client = self._get_s3_client()
        return client.generate_presigned_url(
            'get_object',
            Params={'Bucket': self.bucket_name, 'Key': key},
            ExpiresIn=expires_in,
        )

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


