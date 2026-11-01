"""Centralized exception handlers for GrabItDown API."""

import logging
from fastapi import Request, status
from fastapi.responses import JSONResponse

from src.exceptions import GrabItDownError

logger = logging.getLogger(__name__)


def grabitdown_exception_handler(
    request: Request, exc: GrabItDownError
) -> JSONResponse:
    """Handle GrabItDown domain exceptions."""
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        content={
            "success": False,
            "error": str(exc),
            "error_code": getattr(exc, "error_code", "GID_ERROR"),
        },
    )


def ssrf_security_exception_handler(
    request: Request, exc: Exception
) -> JSONResponse:
    """Handle SSRF validation exceptions with HTTP 400."""
    logger.warning("SSRF security policy blocked request: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_400_BAD_REQUEST,
        content={
            "success": False,
            "error": str(exc),
            "error_code": "SSRF_SECURITY_ERROR",
        },
    )



def generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unexpected exceptions."""
    logger.exception("Unhandled exception: %s", exc)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "success": False,
            "error": "Internal server error",
            "error_code": "INTERNAL_ERROR",
        },
    )
