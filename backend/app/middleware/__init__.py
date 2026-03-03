"""Middleware for the IMK backend."""

from .error_handler import error_handler_middleware

__all__ = ["error_handler_middleware"]
