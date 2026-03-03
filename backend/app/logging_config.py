"""Structured logging configuration using structlog.

Provides JSON-formatted logs with:
- Timestamp
- Log level
- Logger name
- Message
- Contextual key-value pairs
- Exception info
"""

import logging
import logging.handlers
import sys
from pathlib import Path

import structlog


def configure_logging(log_dir: Path | None = None, log_level: str = "INFO") -> None:
    """Configure structured logging for the application.

    Args:
        log_dir: Directory for log files. If None, uses backend/logs/
        log_level: Logging level (DEBUG, INFO, WARNING, ERROR, CRITICAL)
    """
    if log_dir is None:
        log_dir = Path(__file__).resolve().parent.parent / "logs"
    log_dir.mkdir(exist_ok=True)

    # ── Standard library logging configuration ──

    # Remove all existing handlers
    logging.root.handlers.clear()

    # Set root level
    level = getattr(logging, log_level.upper(), logging.INFO)
    logging.root.setLevel(level)

    # Console handler (JSON format for production, human-readable for dev)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)

    # File handler (rotating, JSON format)
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir / "backend.log",
        maxBytes=10 * 1024 * 1024,  # 10 MB
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(level)

    # Add handlers to root logger
    logging.root.addHandler(console_handler)
    logging.root.addHandler(file_handler)

    # ── Structlog configuration ──

    # Shared processors for all loggers
    shared_processors = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
    ]

    # Development: colorized console output
    if sys.stdout.isatty():
        structlog.configure(
            processors=[
                *shared_processors,
                structlog.dev.ConsoleRenderer(colors=True),
            ],
            wrapper_class=structlog.stdlib.BoundLogger,
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )
    else:
        # Production: JSON output
        structlog.configure(
            processors=[
                *shared_processors,
                structlog.processors.format_exc_info,
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.stdlib.BoundLogger,
            context_class=dict,
            logger_factory=structlog.stdlib.LoggerFactory(),
            cache_logger_on_first_use=True,
        )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Get a structured logger instance.

    Usage:
        logger = get_logger(__name__)
        logger.info("match_started", match_id=match_id, fighter1=f1, fighter2=f2)
        logger.error("emulator_failed", match_id=match_id, error=str(e), exc_info=True)
    """
    return structlog.get_logger(name)
