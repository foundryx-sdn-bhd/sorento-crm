"""Error handling utilities."""
from fastapi import HTTPException, status
from typing import Optional


class AppException(HTTPException):
    """Custom application exception."""
    
    def __init__(
        self,
        status_code: int,
        message: str,
        detail: Optional[str] = None,
        code: Optional[str] = None,
        details: Optional[dict] = None,
    ):
        body = {
            "message": message,
            "detail": detail,
            "code": code
        }
        # Serialised only when given, so every existing error body is unchanged.
        if details is not None:
            body["details"] = details
        super().__init__(status_code=status_code, detail=body)
        # Also as plain attributes: the wire shape above is what the handler
        # serialises, and a caller that catches this (a service guard's own
        # test, a retry that only acts on one code) should not have to dig
        # through the detail dict to read what it already knows.
        self.code = code
        self.message = message


def handle_not_found(entity: str, entity_id: str) -> AppException:
    """Create a 404 Not Found exception."""
    return AppException(
        status_code=status.HTTP_404_NOT_FOUND,
        message=f"{entity} not found. Someone might have deleted it already.",
        code="NOT_FOUND"
    )


def handle_unauthorized() -> AppException:
    """Create a 401 Unauthorized exception."""
    return AppException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        message="Unauthorized request",
        code="UNAUTHORIZED"
    )


def handle_validation_error(message: str) -> AppException:
    """Create a 400 Bad Request exception."""
    return AppException(
        status_code=status.HTTP_400_BAD_REQUEST,
        message=message,
        code="VALIDATION_ERROR"
    )


def handle_unprocessable(message: str) -> AppException:
    """Create a 422 Unprocessable Entity exception.

    For a request that is well-formed but asks for something the data forbids
    (merging a certificate into itself, or across companies). Several services
    already raise a bare 422 AppException for exactly this; this is the shared
    helper so they do not have to.
    """
    return AppException(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        message=message,
        code="VALIDATION_ERROR"
    )


def handle_conflict(message: str) -> AppException:
    """Create a 409 Conflict exception."""
    return AppException(
        status_code=status.HTTP_409_CONFLICT,
        message=message,
        code="CONFLICT"
    )


def handle_internal_error(message: str = "Oops! Something went wrong. Please try again in a moment.") -> AppException:
    """Create a 500 Internal Server Error exception."""
    return AppException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        message=message,
        code="INTERNAL_ERROR"
    )
