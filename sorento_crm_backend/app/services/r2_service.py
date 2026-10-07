"""Cloudflare R2 storage service.

R2 exposes an S3-compatible API, so boto3 is reused with a custom endpoint.
For URLs returned to clients we generate an S3 presigned URL and rewrite the
host to the bucket's Cloudflare custom domain so the response is served from
the CDN edge instead of the origin S3 endpoint. This mirrors S3Service so
StorageRouter can dispatch to either backend by provider.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse, urlunparse

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

logger = logging.getLogger(__name__)


# R2 buckets are global, but boto3 still requires a region; "auto" matches Cloudflare's docs.
_R2_REGION = "auto"


try:
    from dotenv import load_dotenv

    _override_path = os.environ.get("SORENTO_ENV_FILE")
    if _override_path and Path(_override_path).exists():
        load_dotenv(_override_path, override=True)
    else:
        env_path = Path(__file__).parent.parent.parent / ".env"
        if env_path.exists():
            load_dotenv(env_path)
        else:
            load_dotenv()
except ImportError:
    pass


class R2Service:
    """File operations against a Cloudflare R2 bucket fronted by a custom CDN domain."""

    def __init__(self):
        self.account_id = os.getenv("R2_ACCOUNT_ID")
        self.access_key_id = os.getenv("R2_ACCESS_KEY_ID")
        self.secret_access_key = os.getenv("R2_SECRET_ACCESS_KEY")
        self.bucket_name = os.getenv("R2_BUCKET_NAME")
        cdn_domain = (os.getenv("R2_CDN_DOMAIN") or "").strip().rstrip("/")

        missing = []
        if not (self.account_id and self.account_id.strip()):
            missing.append("R2_ACCOUNT_ID")
        if not (self.access_key_id and self.access_key_id.strip()):
            missing.append("R2_ACCESS_KEY_ID")
        if not (self.secret_access_key and self.secret_access_key.strip()):
            missing.append("R2_SECRET_ACCESS_KEY")
        if not (self.bucket_name and self.bucket_name.strip()):
            missing.append("R2_BUCKET_NAME")
        if not cdn_domain:
            missing.append("R2_CDN_DOMAIN")
        if missing:
            raise ValueError(
                "Cloudflare R2 configuration incomplete: set the following in the backend environment: "
                + ", ".join(missing)
            )

        self.cdn_domain = cdn_domain  # e.g. "cdn.sorento.com"
        self.endpoint_url = f"https://{self.account_id.strip()}.r2.cloudflarestorage.com"

        self.client = boto3.client(
            "s3",
            endpoint_url=self.endpoint_url,
            aws_access_key_id=self.access_key_id,
            aws_secret_access_key=self.secret_access_key,
            region_name=_R2_REGION,
            config=Config(
                retries={"max_attempts": 3, "mode": "standard"},
                signature_version="s3v4",
            ),
        )

    # ------------------------------------------------------------------ uploads

    def upload_fileobj(
        self,
        fileobj,
        key: str,
        content_type: Optional[str] = None,
    ) -> str:
        """Stream a file object to R2 (multipart), never holding it in memory."""
        key = (key or "").lstrip("/")
        if not key:
            raise ValueError("key is required")
        extra: dict = {}
        if content_type:
            extra["ContentType"] = content_type
        try:
            self.client.upload_fileobj(
                fileobj, self.bucket_name, key, ExtraArgs=extra or None
            )
        except (ClientError, BotoCoreError) as e:
            raise Exception(f"Failed to upload file to R2: {e}")
        return key

    def upload_file(
        self,
        file_content: bytes,
        file_path: str,
        content_type: Optional[str] = None,
        signed_url_expires_in: int = 3600,
    ) -> tuple[str, str]:
        """Upload to R2 and return (key, signed CDN URL)."""
        key = (file_path or "").lstrip("/")
        if not key:
            raise ValueError("file_path is required")
        try:
            extra: dict = {}
            if content_type:
                extra["ContentType"] = content_type
            self.client.put_object(
                Bucket=self.bucket_name,
                Key=key,
                Body=file_content,
                **extra,
            )
        except ClientError as e:
            error_message = e.response.get("Error", {}).get("Message", str(e))
            logger.error("R2 upload error: %s", error_message)
            raise Exception(f"Failed to upload file to R2: {error_message}") from e
        except BotoCoreError as e:
            logger.error("R2 connection error: %s", e)
            raise Exception(f"Failed to connect to R2: {e}") from e

        return key, self.get_signed_url(key, expires_in=signed_url_expires_in)

    # ---------------------------------------------------------------- url helpers

    def get_signed_url(self, key: str, expires_in: int = 3600) -> str:
        """Generate an R2 presigned URL served via the Cloudflare custom domain."""
        normalized = (key or "").lstrip("/")
        if not normalized:
            raise ValueError("R2 key is required to generate signed URL")
        try:
            origin_url = self.client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self.bucket_name, "Key": normalized},
                ExpiresIn=expires_in,
            )
        except ClientError as e:
            logger.error("R2 presigned URL error: %s", e)
            raise Exception(f"Failed to generate R2 presigned URL: {e}") from e
        return self._rewrite_host_to_cdn(origin_url)

    def get_cdn_base_url(self, key: str) -> str:
        """Return the stable, non-signed CDN URL for the given R2 key."""
        normalized = (key or "").lstrip("/")
        if not normalized:
            raise ValueError("R2 key is required to build CDN URL")
        return f"https://{self.cdn_domain}/{normalized}"

    def _rewrite_host_to_cdn(self, presigned_url: str) -> str:
        """Replace the R2 origin host with the Cloudflare custom domain.

        Presigned URLs sign over Bucket+Key+query, but not the Host header (with
        s3v4 + virtual-host style). Cloudflare custom domains map directly onto
        the bucket so requests with the rewritten host are accepted by R2 and
        cached at the edge.
        """
        parsed = urlparse(presigned_url)
        path = parsed.path or "/"
        # boto3 builds path-style URLs against the R2 endpoint
        # ("/{bucket}/{key}"), strip the leading bucket segment so the CDN URL
        # matches "/{key}" expected by the custom domain.
        bucket_prefix = f"/{self.bucket_name}/"
        if path.startswith(bucket_prefix):
            path = "/" + path[len(bucket_prefix):]
        return urlunparse(
            (
                "https",
                self.cdn_domain,
                path,
                parsed.params,
                parsed.query,
                parsed.fragment,
            )
        )

    # ------------------------------------------------------------------ misc ops

    def download_file(self, key: str) -> bytes:
        normalized = (key or "").lstrip("/")
        try:
            response = self.client.get_object(Bucket=self.bucket_name, Key=normalized)
            return response["Body"].read()
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            if error_code in ("NoSuchKey", "404"):
                raise Exception(f"File not found in R2: {normalized}") from e
            raise Exception(f"Failed to download file from R2: {e}") from e
        except BotoCoreError as e:
            raise Exception(f"Failed to connect to R2: {e}") from e

    def delete_file(self, key: str) -> bool:
        normalized = (key or "").lstrip("/")
        try:
            self.client.delete_object(Bucket=self.bucket_name, Key=normalized)
            return True
        except ClientError as e:
            error_message = e.response.get("Error", {}).get("Message", str(e))
            raise Exception(f"Failed to delete file from R2: {error_message}") from e
        except BotoCoreError as e:
            raise Exception(f"Failed to connect to R2: {e}") from e

    def copy_file(self, old_key: str, new_key: str) -> None:
        """Server-side copy within the same R2 bucket (no byte download)."""
        src = (old_key or "").lstrip("/")
        dst = (new_key or "").lstrip("/")
        if not src or not dst:
            raise ValueError("copy_file requires both old_key and new_key")
        try:
            self.client.copy_object(
                Bucket=self.bucket_name,
                CopySource={"Bucket": self.bucket_name, "Key": src},
                Key=dst,
            )
        except ClientError as e:
            error_message = e.response.get("Error", {}).get("Message", str(e))
            raise Exception(f"Failed to copy file in R2: {error_message}") from e
        except BotoCoreError as e:
            raise Exception(f"Failed to connect to R2: {e}") from e

    def file_exists(self, key: str) -> bool:
        normalized = (key or "").lstrip("/")
        try:
            self.client.head_object(Bucket=self.bucket_name, Key=normalized)
            return True
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "Unknown")
            if error_code in ("404", "NoSuchKey", "NotFound"):
                return False
            logger.warning("R2 head_object error %s for %s", error_code, normalized)
            return False
        except BotoCoreError:
            return False
