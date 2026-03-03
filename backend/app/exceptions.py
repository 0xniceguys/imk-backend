"""Custom exceptions for the IMK backend.

All exceptions inherit from IMKException and include:
- message: Human-readable error description
- status_code: HTTP status code
- code: Machine-readable error code
- details: Optional additional context
"""

from typing import Any


class IMKException(Exception):
    """Base exception for all IMK errors."""

    def __init__(
        self,
        message: str,
        status_code: int = 500,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.message = message
        self.status_code = status_code
        self.code = code or self.__class__.__name__
        self.details = details or {}


# ── Resource Errors (404) ──


class ResourceNotFoundError(IMKException):
    """Requested resource does not exist."""

    def __init__(self, resource: str, identifier: str | None = None, details: dict[str, Any] | None = None):
        msg = f"{resource} not found"
        if identifier:
            msg += f": {identifier}"
        super().__init__(message=msg, status_code=404, details=details)


class FighterNotFoundError(ResourceNotFoundError):
    """Requested fighter does not exist."""

    def __init__(self, fighter_id: str):
        super().__init__("Fighter", fighter_id)


class MatchNotFoundError(ResourceNotFoundError):
    """Requested match does not exist."""

    def __init__(self, match_id: str):
        super().__init__("Match", match_id)


class BetNotFoundError(ResourceNotFoundError):
    """Requested bet does not exist."""

    def __init__(self, bet_id: str):
        super().__init__("Bet", bet_id)


class UserNotFoundError(ResourceNotFoundError):
    """Requested user does not exist."""

    def __init__(self, user_id: str):
        super().__init__("User", user_id)


class AgentNotFoundError(ResourceNotFoundError):
    """Requested agent does not exist."""

    def __init__(self, agent_id: str):
        super().__init__("Agent", agent_id)


# ── Validation Errors (400) ──


class ValidationError(IMKException):
    """Request validation failed."""

    def __init__(self, message: str, field: str | None = None, details: dict[str, Any] | None = None):
        if field:
            details = details or {}
            details["field"] = field
        super().__init__(message=message, status_code=400, details=details)


class InvalidAgentError(ValidationError):
    """Invalid agent specification."""

    def __init__(self, agent_id: str, reason: str):
        super().__init__(
            message=f"Invalid agent '{agent_id}': {reason}",
            details={"agent_id": agent_id, "reason": reason},
        )


class InvalidSavestateError(ValidationError):
    """Invalid savestate file."""

    def __init__(self, path: str, reason: str):
        super().__init__(
            message=f"Invalid savestate '{path}': {reason}",
            details={"savestate_path": path, "reason": reason},
        )


class InvalidMatchStateError(ValidationError):
    """Match is in wrong state for this operation."""

    def __init__(self, match_id: str, current_state: str, required_state: str):
        super().__init__(
            message=f"Match {match_id} is {current_state}, must be {required_state}",
            details={
                "match_id": match_id,
                "current_state": current_state,
                "required_state": required_state,
            },
        )


class DuplicateFighterError(ValidationError):
    """Fighter already exists with this name/slug."""

    def __init__(self, field: str, value: str):
        super().__init__(
            message=f"Fighter with {field} '{value}' already exists",
            field=field,
            details={"field": field, "value": value},
        )


class InsufficientFundsError(ValidationError):
    """User has insufficient funds for this operation."""

    def __init__(self, available: float, required: float):
        super().__init__(
            message=f"Insufficient funds: have {available:.6f}, need {required:.6f}",
            details={"available": available, "required": required},
        )


class BettingClosedError(ValidationError):
    """Betting is closed for this match."""

    def __init__(self, match_id: str, reason: str = "Match has already started"):
        super().__init__(
            message=f"Betting closed for match {match_id}: {reason}",
            details={"match_id": match_id, "reason": reason},
        )


# ── Authentication & Authorization (401, 403) ──


class AuthenticationError(IMKException):
    """Authentication failed."""

    def __init__(self, message: str = "Authentication required", details: dict[str, Any] | None = None):
        super().__init__(message=message, status_code=401, details=details)


class InvalidTokenError(AuthenticationError):
    """JWT token is invalid or expired."""

    def __init__(self, reason: str = "Invalid or expired token"):
        super().__init__(message=reason, details={"reason": reason})


class AuthorizationError(IMKException):
    """User is not authorized for this operation."""

    def __init__(self, message: str = "Insufficient permissions", details: dict[str, Any] | None = None):
        super().__init__(message=message, status_code=403, details=details)


class AdminRequiredError(AuthorizationError):
    """Admin privileges required."""

    def __init__(self):
        super().__init__(message="Admin privileges required")


# ── Emulator & Match Runner Errors (500, 503) ──


class EmulatorError(IMKException):
    """Emulator operation failed."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message=message, status_code=500, details=details)


class EmulatorStartupError(EmulatorError):
    """Failed to start emulator."""

    def __init__(self, reason: str, details: dict[str, Any] | None = None):
        super().__init__(message=f"Failed to start emulator: {reason}", details=details)


class EmulatorTimeoutError(EmulatorError):
    """Emulator operation timed out."""

    def __init__(self, operation: str, timeout_seconds: float):
        super().__init__(
            message=f"Emulator {operation} timed out after {timeout_seconds}s",
            details={"operation": operation, "timeout": timeout_seconds},
        )


class BridgeConnectionError(EmulatorError):
    """Failed to connect to emulator bridge."""

    def __init__(self, reason: str):
        super().__init__(message=f"Bridge connection failed: {reason}", details={"reason": reason})


class MatchRunnerError(IMKException):
    """Match runner operation failed."""

    def __init__(self, message: str, match_id: str | None = None, details: dict[str, Any] | None = None):
        details = details or {}
        if match_id:
            details["match_id"] = match_id
        super().__init__(message=message, status_code=500, details=details)


class MatchAlreadyRunningError(MatchRunnerError):
    """Match already has an active runner."""

    def __init__(self, match_id: str):
        super().__init__(message=f"Match {match_id} is already running", match_id=match_id)


# ── External Service Errors (502, 503) ──


class ExternalServiceError(IMKException):
    """External service is unavailable or returned an error."""

    def __init__(self, service: str, reason: str, details: dict[str, Any] | None = None):
        details = details or {}
        details["service"] = service
        super().__init__(
            message=f"{service} error: {reason}",
            status_code=503,
            details=details,
        )


class PrivyAuthError(ExternalServiceError):
    """Privy authentication service error."""

    def __init__(self, reason: str):
        super().__init__(service="Privy", reason=reason)


class SolanaRPCError(ExternalServiceError):
    """Solana RPC node error."""

    def __init__(self, reason: str, details: dict[str, Any] | None = None):
        super().__init__(service="Solana RPC", reason=reason, details=details)


# ── Database Errors (500) ──


class DatabaseError(IMKException):
    """Database operation failed."""

    def __init__(self, message: str, details: dict[str, Any] | None = None):
        super().__init__(message=message, status_code=500, details=details)


class IntegrityError(DatabaseError):
    """Database constraint violation."""

    def __init__(self, constraint: str, details: dict[str, Any] | None = None):
        super().__init__(
            message=f"Database integrity error: {constraint}",
            details=details or {"constraint": constraint},
        )
