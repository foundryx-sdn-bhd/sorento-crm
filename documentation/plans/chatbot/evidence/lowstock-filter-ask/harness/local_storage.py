"""Sandbox stand-in for S3/R2 (no cloud credentials here): files land on local disk."""
import os
from app.services import storage_router

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "files")


class LocalBackend:
    def upload_file(self, file_content, file_path, content_type=None, **_):
        path = os.path.join(ROOT, file_path)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(file_content if isinstance(file_content, bytes) else file_content.read())
        return file_path, f"file://{path}"

    def get_signed_url(self, key, expires_in=0, **_):
        return f"http://localhost:8000/__sandbox_files/{key}"

    get_cdn_base_url = get_cloudfront_base_url = lambda self, key: f"http://localhost:8000/__sandbox_files/{key}"

    def download_file(self, key, **_):
        with open(os.path.join(ROOT, key), "rb") as fh:
            return fh.read()


storage_router.get_backend = lambda provider=None: LocalBackend()
