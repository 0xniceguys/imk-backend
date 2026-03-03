"""Error handler middleware for consistent error responses.

All errors are returned in a consistent JSON format:
{
    "error": {
        "code": "ErrorClassName",
        "message": "Human readable description",
        "details": {...}  // optional
    }
}
"""

import logging
from typing import Callable

from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError as SQLAlchemyIntegrityError

from app.exceptions import IMKException, DatabaseError, IntegrityError

logger = logging.getLogger(__name__)


async def error_handler_middleware(request: Request, call_next: Callable) -> Response:
    """Catch all exceptions and return consistent JSON error responses."""
    try:
        return await call_next(request)
    except IMKException as e:
        # Custom IMK exceptions — already have proper structure
        logger.warning(
            f"{e.code} ({e.status_code}): {e.message}",
            extra={
                "code": e.code,
                "status_code": e.status_code,
                "details": e.details,
                "path": request.url.path,
            },
        )
        return JSONResponse(
            status_code=e.status_code,
            content={
                "error": {
                    "code": e.code,
                    "message": e.message,
                    "details": e.details,
                }
            },
        )
    except SQLAlchemyIntegrityError as e:
        # Database constraint violations
        logger.error(f"Database integrity error: {e}", exc_info=True)
        error = IntegrityError(constraint=str(e.orig))
        return JSONResponse(
            status_code=error.status_code,
            content={
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "details": error.details,
                }
            },
        )
    except Exception as e:
        # Unexpected errors — log full traceback and return 500
        logger.exception(
            f"Unhandled exception in {request.method} {request.url.path}",
            extra={"path": request.url.path, "method": request.method},
        )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={
                "error": {
                    "code": "InternalServerError",
                    "message": "An unexpected error occurred",
                    "details": {"type": type(e).__name__},
                }
            },
        )
