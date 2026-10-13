"""Provider-aware storage dispatch for the S3 -> Cloudflare R2 transition.

During the dual-storage period each attachment row carries a ``storage_provider``
('s3' or 'r2') indicating where its bytes live. Read paths (preview, download,
external presigned URL, webhooks) call into this module instead of either
service directly so the right backend is hit per record. New uploads are
routed to ``STORAGE_DEFAULT_PROVIDER`` (defaults to 's3' until R2 is ready;
flip to 'r2' to migrate new traffic over).
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Optional, Protocol
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeout
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)


PROVIDER_S3 = "s3"
PROVIDER_R2 = "r2"
_VALID_PROVIDERS = {PROVIDER_S3, PROVIDER_R2}

_env_loaded = False


class StorageBackend(Protocol):
    """Common surface implemented by S3Service and R2Service."""

    def upload_file(
        self,
        file_content: bytes,
        file_path: str,
        content_type: Optional[str] = ...,
        signed_url_expires_in: int = ...,
    ) -> tuple[str, str]: ...

    def upload_fileobj(
        self, fileobj, key: str, content_type: Optional[str] = ...
    ) -> str: ...

    def get_signed_url(self, key: str, expires_in: int = ...) -> str: ...

    def download_file(self, key: str) -> bytes: ...

    def delete_file(self, key: str) -> bool: ...

    def file_exists(self, key: str) -> bool: ...

    def copy_file(self, old_key: str, new_key: str) -> None: ...


def sanitize_storage_filename(name: Optional[str]) -> str:
    """Single source of truth for turning a display name into a storage-safe basename.

    Mirrors the upload path exactly so rename and upload can never drift. Keeps
    alphanumerics, space, dash, underscore, dot; collapses everything else away.
    """
    base = "".join(
        c for c in (name or "") if c.isalnum() or c in (" ", "-", "_", ".")
    ).strip()
    return base or "file"


def normalize_provider(value: Optional[str]) -> str:
    """Coerce stored provider values to one of the canonical names; default to S3."""
    if not value:
        return PROVIDER_S3
    v = str(value).strip().lower()
    return v if v in _VALID_PROVIDERS else PROVIDER_S3


def _ensure_env_loaded() -> None:
    """Populate os.environ from the backend .env once, mirroring s3_service/r2_service.

    Only ``app.main`` calls ``load_dotenv``, so processes that never import it -
    above all ``worker.py``, which owns every export/import job - saw an empty
    ``STORAGE_DEFAULT_PROVIDER`` and silently fell back to S3. On a host without
    the CloudFront signing key that turned into "CloudFront private key file not
    found" on a stack configured for R2. Reading the file here makes the provider
    resolve identically in the API and the worker.
    """
    global _env_loaded
    if _env_loaded:
        return
    _env_loaded = True
    try:
        from pathlib import Path

        from dotenv import load_dotenv

        env_path = Path(__file__).parent.parent.parent / ".env"
        if env_path.exists():
            load_dotenv(env_path)
        else:
            load_dotenv()
    except ImportError:  # python-dotenv absent: rely on a real environment
        logger.warning("python-dotenv not installed; STORAGE_DEFAULT_PROVIDER must be exported")


def default_provider() -> str:
    """Provider used when a caller has no row context (only a raw file_path)."""
    _ensure_env_loaded()
    return normalize_provider(os.getenv("STORAGE_DEFAULT_PROVIDER", PROVIDER_S3))


_backends: dict[str, StorageBackend] = {}
_backend_lock = threading.Lock()


def _build_backend(p: str) -> StorageBackend:
    if p == PROVIDER_R2:
        from app.services.r2_service import R2Service

        return R2Service()
    from app.services.s3_service import S3Service

    return S3Service()


def get_backend(provider: Optional[str]) -> StorageBackend:
    """Return the storage service for the given provider, built once per process.

    This used to construct a brand-new service on EVERY call, and every call site
    (presign, preview, download, upload, webhook) pays that. Constructing one is
    not cheap and none of it is per-request work:

      * ``boto3.client(...)`` - ~350ms on the first build in a process (botocore
        loads its service model JSON from disk), ~3ms on every build after.
      * ``S3Service`` additionally builds a ``CloudFrontSigner``, which reads the
        RSA private key from disk and parses it - ~225ms EVERY time, because the
        parse is not cached anywhere. Its own docstring says "key loaded once",
        which was true per instance and defeated by rebuilding the instance per
        request.

    So a presign was ~99% setup and ~1% signing, and an n8n loop over N
    attachments paid it N times. Caching makes it once per process.

    Safe to share: boto3 clients are documented as thread-safe (it is *resources*
    that are not), and the cryptography key object is only used for signing. Both
    services are stateless after construction.
    """
    p = normalize_provider(provider)
    cached = _backends.get(p)
    if cached is not None:
        return cached
    with _backend_lock:
        cached = _backends.get(p)  # another thread may have built it while we waited
        if cached is None:
            cached = _build_backend(p)
            _backends[p] = cached
        return cached


def warm_backends() -> None:
    """Build the default provider's backend at startup.

    Without this the cost above lands on whichever unlucky request arrives first
    after a worker starts - and a worker that keeps being recycled pays it again
    every time, so under load the "first request" penalty is not rare.
    Best-effort: a misconfigured provider must not stop the app booting, it will
    surface on the first real call exactly as it does today.
    """
    try:
        get_backend(default_provider())
    except Exception as exc:  # noqa: BLE001
        logger.warning("storage backend warm-up skipped: %s", exc)


def cdn_base_url(provider: str, key: str) -> str:
    """Return the stable, non-signed CDN URL stored on the DB row."""
    p = normalize_provider(provider)
    backend = get_backend(p)
    if p == PROVIDER_R2:
        return backend.get_cdn_base_url(key)  # type: ignore[attr-defined]
    return backend.get_cloudfront_base_url(key)  # type: ignore[attr-defined]


def detect_provider_from_url(file_path: Optional[str]) -> Optional[str]:
    """Return 's3' or 'r2' if the URL host matches a known CDN domain, else None.

    Used as a fallback when only a file_path string is available (e.g. the
    external /presigned_url endpoint or the avatar resolver, where the row
    isn't always loaded).
    """
    if not file_path:
        return None
    raw = str(file_path).strip()
    if not (raw.startswith("https://") or raw.startswith("http://")):
        return None
    host = (urlparse(raw).hostname or "").lower()
    if not host:
        return None

    cf_host = (os.getenv("CLOUDFRONT_DOMAIN") or "").strip().lower().rstrip("/")
    r2_host = (os.getenv("R2_CDN_DOMAIN") or "").strip().lower().rstrip("/")
    if r2_host and host == r2_host:
        return PROVIDER_R2
    if cf_host and host == cf_host:
        return PROVIDER_S3
    return None


def extract_key(file_path: Optional[str]) -> Optional[str]:
    """Strip scheme/host/query from a stored file_path to recover the storage key."""
    if not file_path:
        return None
    raw = str(file_path).strip()
    if not raw:
        return None
    if raw.startswith("https://") or raw.startswith("http://"):
        parsed = urlparse(raw)
        return unquote((parsed.path or "").lstrip("/")) or None
    return unquote(raw).lstrip("/") or None


"""Signed URLs, memoised for a fraction of their own lifetime.

Signing is not free and it is asked for in BULK. One published catalogue page
signs a photo for every product on it - 439 on the seeded A3 brochure - and that
was 3.0 of the 3.2 seconds the whole request took, dwarfing the three database
queries that produce the content. The same shape shows up in attachment lists
and product pickers.

The URL for a given key does not depend on who is asking: these are key-pair
signed CloudFront URLs and R2 presigned URLs, not per-user grants. So the same
key asked for twice inside a few minutes can safely be answered twice with the
same string.

**TTL is a fraction of `expires_in`, never equal to it.** A cached URL must
always have most of its life ahead of it, or a reader could be handed one with
seconds left and see the image 403 as they scroll to it.
"""

_MISS = object()

# A sixth of the URL's own lifetime: at the default hour that is ten minutes,
# and the URL a reader receives always has at least fifty minutes left.
_SIGNED_TTL_DIVISOR = 6
# Bounded so a long-running worker touching every attachment cannot grow this
# without limit. Entries are short strings.
_SIGNED_CACHE_MAX = 4096

_signed_lock = threading.Lock()
_signed_cache: dict[tuple[str, str, int], tuple[float, Optional[str]]] = {}


def _signed_cache_get(provider: str, key: str, expires_in: int):
    with _signed_lock:
        entry = _signed_cache.get((provider, key, expires_in))
        if entry is None:
            return _MISS
        expires_at, value = entry
        if expires_at <= time.monotonic():
            _signed_cache.pop((provider, key, expires_in), None)
            return _MISS
        return value


def _signed_cache_put(provider: str, key: str, expires_in: int, value: Optional[str]) -> None:
    ttl = max(1, expires_in // _SIGNED_TTL_DIVISOR)
    with _signed_lock:
        if len(_signed_cache) >= _SIGNED_CACHE_MAX:
            # Every entry of a given expires_in shares a TTL, so expiry order is
            # insertion order and this is the cheapest correct eviction.
            oldest = min(_signed_cache, key=lambda item: _signed_cache[item][0])
            _signed_cache.pop(oldest, None)
        _signed_cache[(provider, key, expires_in)] = (time.monotonic() + ttl, value)


def clear_signed_url_cache() -> None:
    """For tests, and for a caller that has just replaced an object's bytes."""
    with _signed_lock:
        _signed_cache.clear()


#: D9 (owner console pass, 8 Sep 2026): signing is BEST EFFORT. One attempt, this many
#: seconds per file, no retry - a signer that hangs (turn 8f4356a3 waited out the lane's
#: whole 10 s tool budget) costs one link, never the answer.
SIGN_TIMEOUT_SECONDS = 2.0
_SIGNER = ThreadPoolExecutor(max_workers=4, thread_name_prefix="sign-url")


def _sign_bounded(provider: str, key: str, expires_in: int) -> Optional[str]:
    """`get_backend(provider).get_signed_url(...)` under `SIGN_TIMEOUT_SECONDS`. Raises
    `TimeoutError` when the signer does not answer in time (the caller treats it like any
    other signing failure: warns, caches the miss, hands back None or the raw path)."""
    future = _SIGNER.submit(lambda: get_backend(provider).get_signed_url(key, expires_in=expires_in))
    try:
        return future.result(timeout=SIGN_TIMEOUT_SECONDS)
    except FuturesTimeout as exc:
        raise TimeoutError(f"signing took longer than {SIGN_TIMEOUT_SECONDS:g}s") from exc


def resolve_signed_url(
    file_path: Optional[str],
    *,
    provider: Optional[str] = None,
    expires_in: int = 3600,
    strict: bool = False,
) -> Optional[str]:
    """Return a fresh signed URL for the stored file_path.

    Dispatch order:
      1. If ``provider`` is given (caller has the row), use it directly.
      2. Otherwise, sniff provider from the URL hostname.
      3. Otherwise, fall back to ``STORAGE_DEFAULT_PROVIDER``.

    Already-signed URLs (CloudFront Policy/Key-Pair-Id query params or R2
    AWS4-HMAC-SHA256 signatures) are passed through unchanged when ``provider``
    cannot be determined and the URL clearly carries a live signature.

    ``strict`` decides what an UNSIGNABLE file is worth. By default this
    function fails open: signing raises, it logs, and it hands back the raw
    path. For a download link that is a fair last resort, because the user gets
    something to try and ten call sites depend on it.

    For an IMAGE it is the wrong answer. Nothing tries anything: the browser
    requests the unsigned URL, the CDN answers 403, and the reader gets a broken
    image where the surface already has a designed no-image state. So callers
    that render an image pass ``strict=True`` and treat a signing failure as an
    absent image. Not hypothetical: 181 of 2,472 linked product images could not
    be signed on one environment, and they rendered as broken tiles rather than
    as the empty state.
    """
    if not file_path:
        return None if strict else file_path

    raw = str(file_path).strip()
    if not raw:
        return None if strict else file_path

    chosen = (
        normalize_provider(provider)
        if provider is not None
        else (detect_provider_from_url(raw) or default_provider())
    )

    # If caller explicitly supplied a provider we always re-sign so expired
    # query strings don't leak through. Otherwise we treat an already-signed
    # URL as good enough to return as-is when provider is ambiguous.
    if provider is None and _looks_signed(raw) and detect_provider_from_url(raw) is None:
        return raw

    key = extract_key(raw)
    if not key:
        return None if strict else raw

    cached = _signed_cache_get(chosen, key, expires_in)
    if cached is not _MISS:
        return cached if cached is not None else (None if strict else raw)

    try:
        signed = _sign_bounded(chosen, key, expires_in)
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Signed URL generation failed for provider=%s key=%s: %s",
            chosen,
            (key or "")[:80],
            e,
        )
        # Cached as a failure, not re-attempted per call. A key that cannot be
        # signed now will not start signing a millisecond later, and the
        # catalogue asks about hundreds of them in one request.
        _signed_cache_put(chosen, key, expires_in, None)
        return None if strict else raw

    _signed_cache_put(chosen, key, expires_in, signed)
    return signed


def copy_object_verified(provider: str, old_key: str, new_key: str) -> None:
    """Server-side copy old_key -> new_key, then verify new exists. No byte download.

    Raises AppException(409) if an object already lives at new_key (never clobber),
    AppException(500) if the copy can't be verified. Does NOT delete the old object - 
    caller deletes after its DB commit so the DB never points at a missing object.
    """
    from app.services.error_handler import AppException

    old = (old_key or "").lstrip("/")
    new = (new_key or "").lstrip("/")
    if not old or not new:
        raise AppException(
            status_code=500,
            message="Storage rename requires both source and target keys.",
            code="STORAGE_RENAME_BAD_KEY",
        )
    if old == new:
        return
    backend = get_backend(provider)
    if backend.file_exists(new):
        raise AppException(
            status_code=409,
            message="A file already exists at the target name.",
            code="ATTACHMENT_FILENAME_COLLISION",
        )
    backend.copy_file(old, new)
    if not backend.file_exists(new):
        raise AppException(
            status_code=500,
            message="Storage copy could not be verified.",
            code="STORAGE_COPY_UNVERIFIED",
        )


def delete_object_best_effort(provider: str, key: str) -> None:
    """Delete an object, swallowing failure (leaves an orphan, never breaks the request)."""
    k = (key or "").lstrip("/")
    if not k:
        return
    try:
        get_backend(provider).delete_file(k)
    except Exception as e:  # noqa: BLE001
        logger.warning("delete_object_best_effort: delete of %s failed (orphan left): %s", k, e)


def _looks_signed(url: str) -> bool:
    """Heuristic: CloudFront uses Policy/Key-Pair-Id, R2/S3 v4 uses X-Amz-Signature."""
    return (
        ("Policy=" in url and "Key-Pair-Id=" in url)
        or "X-Amz-Signature=" in url
    )
