"""Structured logging for API requests."""
import logging
import json
from datetime import datetime
from typing import Optional
from fastapi import Request
from sqlalchemy.orm import Session
from app.models.user import SystemLog

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)


async def log_api_request(
    request: Request,
    user_id: Optional[str],
    db: Session,
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    event: Optional[str] = None,
    description: Optional[str] = None,
    ip_address: Optional[str] = None
):
    """
    Log API request to system_logs table.
    
    Args:
        request: FastAPI request object
        user_id: User ID from JWT token
        db: Database session
        entity_type: Type of entity being accessed (e.g., 'product', 'order')
        entity_id: ID of entity being accessed
        event: Event type (e.g., 'create', 'update', 'delete', 'read')
        description: Additional description
        ip_address: Client IP address
    """
    try:
        # Get IP address from request if not provided
        if not ip_address:
            from app.services.client_ip import client_ip

            ip_address = client_ip(request) or "unknown"
        
        # Determine event type from request method if not provided
        if not event:
            event_map = {
                "GET": "read",
                "POST": "create",
                "PUT": "update",
                "PATCH": "update",
                "DELETE": "delete",
            }
            event = event_map.get(request.method, "unknown")
        
        # Create system log entry
        system_log = SystemLog(
            user_id=user_id or "system",
            entity_type=entity_type,
            entity_id=entity_id,
            event=event,
            description=description or f"{request.method} {request.url.path}",
            ip_address=ip_address,
            meta=json.dumps({
                "method": request.method,
                "path": request.url.path,
                "query_params": str(request.query_params),
            })
        )
        
        db.add(system_log)
        db.commit()
        
        # Also log to application logger
        logger.info(
            f"API Request: {request.method} {request.url.path} - "
            f"User: {user_id} - Event: {event}"
        )
    except Exception as e:
        # Don't fail the request if logging fails
        logger.error(f"Failed to log API request: {str(e)}")
        db.rollback()
