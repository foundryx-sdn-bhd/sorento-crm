"""S3 service for file storage operations.

Uploads files to S3 and returns CloudFront signed URLs suitable for external
use (e.g. sending in respond.io / WhatsApp messages). Requires CloudFront
key group configuration via environment variables.
"""
import os
import logging
from typing import Optional
from pathlib import Path
import boto3
from botocore.exceptions import ClientError, BotoCoreError
from botocore.config import Config

from app.services.cloudfront_signer import CloudFrontSigner

logger = logging.getLogger(__name__)

# Default path for CloudFront private key if CLOUDFRONT_PRIVATE_KEY_PATH not set
_DEFAULT_CLOUDFRONT_PRIVATE_KEY_PATH = "./cloudfront_private_key.pem"

# Load environment variables from .env file if it exists
try:
    from dotenv import load_dotenv
    # Load .env file from the backend directory
    env_path = Path(__file__).parent.parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        logger.debug(f"Loaded .env file from {env_path}")
    else:
        # Try loading from current directory
        load_dotenv()
        logger.debug("Loaded .env file from current directory")
except ImportError:
    # python-dotenv not installed, rely on environment variables being set
    logger.warning("python-dotenv not installed. Environment variables must be set manually.")


class S3Service:
    """Service for S3 file operations."""
    
    def __init__(self):
        """Initialize S3 client from environment variables."""
        self.access_key_id = os.getenv("AWS_ACCESS_KEY_ID")
        self.secret_access_key = os.getenv("AWS_SECRET_ACCESS_KEY")
        self.region = os.getenv("AWS_REGION", "us-east-1")
        self.bucket_name = os.getenv("AWS_S3_BUCKET_NAME")
        
        missing = []
        if not (self.access_key_id and self.access_key_id.strip()):
            missing.append("AWS_ACCESS_KEY_ID")
        if not (self.secret_access_key and self.secret_access_key.strip()):
            missing.append("AWS_SECRET_ACCESS_KEY")
        if not (self.bucket_name and self.bucket_name.strip()):
            missing.append("AWS_S3_BUCKET_NAME")
        if missing:
            raise ValueError(
                "S3 configuration incomplete: set the following in the backend environment: "
                + ", ".join(missing)
            )
        
        # Configure boto3 with retry settings
        config = Config(
            retries={
                'max_attempts': 3,
                'mode': 'standard'
            }
        )
        
        self.s3_client = boto3.client(
            's3',
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
            region_name=self.region,
            config=config
        )

        # CloudFront signer: single instance, key loaded once (fail fast if config missing)
        cloudfront_domain = os.getenv("CLOUDFRONT_DOMAIN")
        cloudfront_key_pair_id = os.getenv("CLOUDFRONT_KEY_PAIR_ID")
        cloudfront_private_key_path = os.getenv(
            "CLOUDFRONT_PRIVATE_KEY_PATH", _DEFAULT_CLOUDFRONT_PRIVATE_KEY_PATH
        )
        missing_cf = []
        if not (cloudfront_domain and cloudfront_domain.strip()):
            missing_cf.append("CLOUDFRONT_DOMAIN")
        if not (cloudfront_key_pair_id and cloudfront_key_pair_id.strip()):
            missing_cf.append("CLOUDFRONT_KEY_PAIR_ID")
        if not (cloudfront_private_key_path and cloudfront_private_key_path.strip()):
            missing_cf.append("CLOUDFRONT_PRIVATE_KEY_PATH")
        if missing_cf:
            raise ValueError(
                "CloudFront configuration incomplete: set the following in the backend environment: "
                + ", ".join(missing_cf)
            )
        self._cloudfront_signer: CloudFrontSigner = CloudFrontSigner(
            key_pair_id=cloudfront_key_pair_id.strip(),
            private_key_path=cloudfront_private_key_path.strip(),
            dist_domain=cloudfront_domain.strip(),
        )

    def upload_fileobj(
        self,
        fileobj,
        key: str,
        content_type: Optional[str] = None,
    ) -> str:
        """Stream a file object to S3 (multipart), never holding it in memory."""
        key = (key or "").lstrip("/")
        if not key:
            raise ValueError("key is required")
        extra: dict = {}
        if content_type:
            extra["ContentType"] = content_type
        try:
            self.s3_client.upload_fileobj(
                fileobj, self.bucket_name, key, ExtraArgs=extra or None
            )
        except (ClientError, BotoCoreError) as e:
            raise Exception(f"Failed to upload file to S3: {e}")
        return key

    def upload_file(
        self,
        file_content: bytes,
        file_path: str,
        content_type: Optional[str] = None,
        signed_url_expires_in: int = 3600,
    ) -> tuple[str, str]:
        """
        Upload file to S3 and return a CloudFront signed URL.

        The returned URL is suitable for external use (e.g. respond.io / WhatsApp).

        Args:
            file_content: File content as bytes
            file_path: S3 key (path) where file will be stored
            content_type: MIME type of the file (optional)
            signed_url_expires_in: Expiry in seconds for the CloudFront signed URL (default 3600)

        Returns:
            Tuple of (S3 key, CloudFront signed URL) where file was stored

        Raises:
            Exception: If upload or signing fails

        Example:
            s3_service = S3Service()
            key, url = s3_service.upload_file(file_bytes, "videos/demo.mp4", content_type="video/mp4")
            # url is a CloudFront signed URL suitable to send to respond.io
        """
        try:
            extra_args = {}
            if content_type:
                extra_args['ContentType'] = content_type

            self.s3_client.put_object(
                Bucket=self.bucket_name,
                Key=file_path,
                Body=file_content,
                **extra_args
            )

            # Return CloudFront signed URL instead of raw S3 URL
            cloudfront_url = self._cloudfront_signer.generate_url(
                file_path, expires_in=signed_url_expires_in
            )

            logger.info(
                "Successfully uploaded file to S3: %s (CloudFront signed URL generated)",
                file_path,
            )
            return file_path, cloudfront_url

        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            logger.error(f"S3 upload error ({error_code}): {error_message}")
            raise Exception(f"Failed to upload file to S3: {error_message}")
        
        except BotoCoreError as e:
            logger.error(f"S3 connection error: {str(e)}")
            raise Exception(f"Failed to connect to S3: {str(e)}")

    def get_signed_url(self, s3_key: str, expires_in: int = 3600) -> str:
        """
        Generate a fresh CloudFront signed URL for an S3 key.
        Use this when returning attachment links so the path always matches the object and the URL is not expired.
        """
        key = (s3_key or "").lstrip("/")
        if not key:
            raise ValueError("S3 key is required to generate signed URL")
        return self._cloudfront_signer.generate_url(key, expires_in=expires_in)

    def get_cloudfront_base_url(self, s3_key: str) -> str:
        """
        Build the CloudFront base URL for an S3 key (https://domain/key).
        Use this when storing file_path so the DB format matches other attachments.
        """
        key = (s3_key or "").lstrip("/")
        if not key:
            raise ValueError("S3 key is required to build CloudFront URL")
        return f"https://{self._cloudfront_signer.dist_domain}/{key}"

    def get_file_url(self, file_path: str) -> str:
        """
        Get the S3 URL for a file.
        
        Args:
            file_path: S3 key (path) of the file
        
        Returns:
            S3 URL (https://...)
        """
        # Construct S3 URL
        # Format: https://{bucket}.s3.{region}.amazonaws.com/{key}
        # Or: https://s3.{region}.amazonaws.com/{bucket}/{key}
        if self.region == "us-east-1":
            # us-east-1 uses a different URL format
            url = f"https://{self.bucket_name}.s3.amazonaws.com/{file_path}"
        else:
            url = f"https://{self.bucket_name}.s3.{self.region}.amazonaws.com/{file_path}"
        
        return url
    
    def download_file(self, file_path: str) -> bytes:
        """
        Download file from S3.
        
        Args:
            file_path: S3 key (path) of the file to download
        
        Returns:
            File content as bytes
        
        Raises:
            Exception: If download fails or file not found
        """
        try:
            response = self.s3_client.get_object(
                Bucket=self.bucket_name,
                Key=file_path
            )
            
            file_content = response['Body'].read()
            logger.info(f"Successfully downloaded file from S3: {file_path}")
            return file_content
        
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            
            if error_code == 'NoSuchKey':
                logger.error(f"File not found in S3: {file_path}")
                raise Exception(f"File not found: {file_path}")
            
            logger.error(f"S3 download error ({error_code}): {error_message}")
            raise Exception(f"Failed to download file from S3: {error_message}")
        
        except BotoCoreError as e:
            logger.error(f"S3 connection error: {str(e)}")
            raise Exception(f"Failed to connect to S3: {str(e)}")
    
    def delete_file(self, file_path: str) -> bool:
        """
        Delete file from S3.
        
        Args:
            file_path: S3 key (path) of the file to delete
        
        Returns:
            True if deletion was successful
        
        Raises:
            Exception: If deletion fails
        """
        try:
            self.s3_client.delete_object(
                Bucket=self.bucket_name,
                Key=file_path
            )
            
            logger.info(f"Successfully deleted file from S3: {file_path}")
            return True
        
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            logger.error(f"S3 delete error ({error_code}): {error_message}")
            raise Exception(f"Failed to delete file from S3: {error_message}")
        
        except BotoCoreError as e:
            logger.error(f"S3 connection error: {str(e)}")
            raise Exception(f"Failed to connect to S3: {str(e)}")
    
    def get_presigned_url(
        self,
        file_path: str,
        expiration: int = 3600
    ) -> str:
        """
        Generate presigned URL for direct file access.
        
        Args:
            file_path: S3 key (path) of the file
            expiration: URL expiration time in seconds (default: 1 hour)
        
        Returns:
            Presigned URL string
        
        Raises:
            Exception: If URL generation fails
        """
        try:
            url = self.s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': self.bucket_name,
                    'Key': file_path
                },
                ExpiresIn=expiration
            )
            
            logger.info(f"Generated presigned URL for: {file_path}")
            return url
        
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            logger.error(f"S3 presigned URL error ({error_code}): {error_message}")
            raise Exception(f"Failed to generate presigned URL: {error_message}")
        
        except BotoCoreError as e:
            logger.error(f"S3 connection error: {str(e)}")
            raise Exception(f"Failed to connect to S3: {str(e)}")
    
    def copy_file(self, old_key: str, new_key: str) -> None:
        """Server-side copy within the same S3 bucket (no byte download)."""
        src = (old_key or "").lstrip("/")
        dst = (new_key or "").lstrip("/")
        if not src or not dst:
            raise ValueError("copy_file requires both old_key and new_key")
        try:
            self.s3_client.copy_object(
                Bucket=self.bucket_name,
                CopySource={"Bucket": self.bucket_name, "Key": src},
                Key=dst,
            )
            logger.info(f"Successfully copied S3 object {src} -> {dst}")
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            error_message = e.response.get('Error', {}).get('Message', str(e))
            logger.error(f"S3 copy error ({error_code}): {error_message}")
            raise Exception(f"Failed to copy file in S3: {error_message}")
        except BotoCoreError as e:
            logger.error(f"S3 connection error: {str(e)}")
            raise Exception(f"Failed to connect to S3: {str(e)}")

    def file_exists(self, file_path: str) -> bool:
        """
        Check if file exists in S3.
        
        Args:
            file_path: S3 key (path) of the file
        
        Returns:
            True if file exists, False otherwise
        """
        try:
            self.s3_client.head_object(
                Bucket=self.bucket_name,
                Key=file_path
            )
            return True
        
        except ClientError as e:
            error_code = e.response.get('Error', {}).get('Code', 'Unknown')
            if error_code == '404':
                return False
            # For other errors, log and return False
            logger.warning(f"Error checking file existence: {error_code}")
            return False
        
        except BotoCoreError:
            return False
