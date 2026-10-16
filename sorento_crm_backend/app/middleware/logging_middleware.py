"""Logging middleware for API requests."""
import re
import time
import uuid
import logging
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from app.services.logging import log_api_request
from app.audit_context import AuditActor, stamp_actor, set_trace_id, start_request_context

# An inbound X-Trace-Id is echoed in the response and written to every audit row, so
# only a plain token is accepted; anything else gets a freshly minted id.
_TRACE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# Paths whose unauthenticated writes are a public link's (identity S0, plan 8.1).
_PUBLIC_PREFIX = "/api/v1/public/"

logger = logging.getLogger(__name__)


class LoggingMiddleware(BaseHTTPMiddleware):
    """Middleware to log all API requests and set request-scoped audit context (IP)."""
    
    async def dispatch(self, request: Request, call_next):
        start_time = time.time()
        # Reset and default-stamp the audit actor before anything else runs, so no
        # request inherits a previous one's. The auth dependencies stamp the real
        # principal over this; a request that never authenticates keeps it.
        ip = request.client.host if request.client else None
        stamp_actor(
            AuditActor(
                actor_type="public_link" if request.url.path.startswith(_PUBLIC_PREFIX) else "system",
                ip_address=ip,
                user_agent=request.headers.get("user-agent"),
            ),
            request=request,
        )
        # Correlation id for this request (honour an inbound X-Trace-Id if present,
        # else mint one). Copied onto every audit row written during the request.
        inbound = request.headers.get("X-Trace-Id")
        trace_id = inbound if inbound and _TRACE_ID_RE.match(inbound) else uuid.uuid4().hex[:16]
        set_trace_id(trace_id)
        # One fresh, MUTABLE business context per request (#1281 S0): event, reason, source,
        # correlation id. An inbound X-Correlation-Id (the header api_call_log reads) is held
        # aside and trusted only once an integration key authenticates.
        inbound_correlation = request.headers.get("X-Correlation-Id")
        start_request_context(
            inbound_correlation if inbound_correlation and _TRACE_ID_RE.match(inbound_correlation) else None
        )

        # Skip logging for health check and docs
        if request.url.path in ["/health", "/health/deep", "/docs", "/redoc", "/openapi.json"]:
            response = await call_next(request)
            response.headers["X-Trace-Id"] = trace_id
            return response

        # Process request
        response = await call_next(request)
        response.headers["X-Trace-Id"] = trace_id
        
        # Calculate duration
        process_time = time.time() - start_time
        
        # Log request (async, don't block response)
        try:
            # Extract user from request state if available
            user_id = getattr(request.state, "user_id", None)
            
            # Log to system_logs table (fire and forget)
            # Note: This requires database session, which we'll handle in a background task
            # For now, just log to application logger
            logger.info(
                f"{request.method} {request.url.path} - "
                f"Status: {response.status_code} - "
                f"Duration: {process_time:.3f}s"
            )
        except Exception as e:
            logger.error(f"Failed to log request: {str(e)}")
        
        return response
