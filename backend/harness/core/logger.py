"""
Structured logging system for Harness application using Loguru.

Features:
- Console and file logging with rotation
- Request context tracking with unique IDs
- Execution time decorators for performance monitoring
- Structured JSON logging for production
- Separate error log file
"""

import asyncio
import functools
import os
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Dict, Optional, TypeVar

from loguru import logger

# Type variables for decorator support
F = TypeVar('F', bound=Callable[..., Any])
T = TypeVar('T')


def setup_logger(log_level: Optional[str] = None, environment: Optional[str] = None) -> None:
    """
    Configure Loguru logger with console and file handlers.

    Removes default handler, adds:
    - Console handler with colorized output
    - Combined log file with rotation (10 MB, 7 days)
    - Separate error log file
    - JSON format option for production

    Log files are stored in logs/ directory at project root.

    Args:
        log_level: Optional log level (defaults to INFO)
        environment: Optional environment name (defaults to development)
    """
    # Get config values or defaults to avoid circular import
    log_level = log_level or os.getenv("LOG_LEVEL", "INFO")
    environment = environment or os.getenv("ENVIRONMENT", "development")

    # Remove default handler
    logger.remove()

    # Determine log directory (backend/logs/)
    log_dir = Path(__file__).parent.parent.parent / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)

    # Console handler - detailed format with colors
    logger.add(
        sys.stdout,
        format=(
            "<green>{time:YYYY-MM-DD HH:mm:ss.SSS}</green> | "
            "<level>{level: <8}</level> | "
            "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
            "<level>{message}</level>"
        ),
        level=log_level,
        colorize=True,
        enqueue=True,  # Thread-safe
        backtrace=True,
        diagnose=True
    )

    # Combined log file with rotation
    logger.add(
        log_dir / "harness.log",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
        level=log_level,
        rotation="10 MB",
        retention="7 days",
        compression="zip",
        enqueue=True,
        backtrace=True,
        diagnose=True
    )

    # Error-only log file
    logger.add(
        log_dir / "errors.log",
        format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level: <8} | {name}:{function}:{line} | {message}",
        level="ERROR",
        rotation="10 MB",
        retention="30 days",
        compression="zip",
        enqueue=True,
        backtrace=True,
        diagnose=True
    )

    # Production JSON logging (optional, based on env)
    if environment == "production":
        logger.add(
            log_dir / "harness.json",
            format="{message}",  # Loguru handles JSON serialization
            level=log_level,
            rotation="50 MB",
            retention="14 days",
            compression="zip",
            enqueue=True,
            serialize=True  # JSON format
        )

    logger.info(f"Logger initialized - Level: {log_level}, Env: {environment}")


def get_logger(module_name: str) -> Any:
    """
    Get a logger instance bound to a specific module.

    Args:
        module_name: Name of the module (typically __name__)

    Returns:
        Bound logger instance with module context

    Example:
        logger = get_logger(__name__)
        logger.info("Processing request", extra={"request_id": "123"})
    """
    return logger.bind(module=module_name)


class RequestContext:
    """
    Context manager for request-scoped logging with unique request IDs.

    Tracks execution time and metadata for individual requests/operations.

    Attributes:
        request_id: Unique identifier for this request
        start_time: Request start timestamp
        metadata: Additional request context
        logger: Bound logger instance

    Example:
        with RequestContext("api_handler", {"user_id": 123}) as ctx:
            ctx.log("info", "Processing request")
            # ... do work ...
        # Automatically logs completion with duration
    """

    def __init__(self, operation: str, metadata: Optional[Dict[str, Any]] = None):
        """
        Initialize request context.

        Args:
            operation: Name of the operation being performed
            metadata: Optional additional context (user_id, endpoint, etc.)
        """
        self.request_id = str(uuid.uuid4())
        self.start_time = time.time()
        self.operation = operation
        self.metadata = metadata or {}
        self.logger = get_logger("request")
        self.completed = False

    def log(self, level: str, message: str, **kwargs: Any) -> None:
        """
        Log a message within this request context.

        Args:
            level: Log level (debug, info, warning, error, critical)
            message: Log message
            **kwargs: Additional fields to include in log entry
        """
        log_method = getattr(self.logger, level.lower(), self.logger.info)
        log_method(
            message,
            request_id=self.request_id,
            operation=self.operation,
            **self.metadata,
            **kwargs
        )

    def complete(self, status: str = "success", **kwargs: Any) -> None:
        """
        Mark request as completed and log final status.

        Args:
            status: Completion status (success, failed, error)
            **kwargs: Additional completion metadata
        """
        if self.completed:
            return

        duration = time.time() - self.start_time
        self.completed = True

        log_level = "error" if status != "success" else "info"
        self.log(
            log_level,
            f"Request completed: {self.operation}",
            status=status,
            duration_seconds=round(duration, 3),
            **kwargs
        )

    def __enter__(self) -> "RequestContext":
        """Enter context manager and log request start."""
        self.logger.info(
            f"Request started: {self.operation}",
            request_id=self.request_id,
            operation=self.operation,
            **self.metadata
        )
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        """
        Exit context manager and log completion.

        If an exception occurred, logs as error with exception details.
        """
        if exc_type is not None:
            self.complete(
                status="error",
                error_type=exc_type.__name__,
                error_message=str(exc_val)
            )
        elif not self.completed:
            self.complete(status="success")


def log_execution_time(operation: Optional[str] = None) -> Callable[[F], F]:
    """
    Decorator to log function execution time.

    Supports both sync and async functions.

    Args:
        operation: Optional operation name (defaults to function name)

    Example:
        @log_execution_time("process_data")
        def my_function():
            # Logs execution time on completion
            pass

        @log_execution_time()
        async def async_function():
            # Works with async too
            pass
    """
    def decorator(func: F) -> F:
        op_name = operation or func.__name__
        module_logger = get_logger(func.__module__)

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            start_time = time.time()
            try:
                module_logger.debug(f"Starting: {op_name}")
                result = func(*args, **kwargs)
                duration = time.time() - start_time
                module_logger.info(
                    f"Completed: {op_name}",
                    operation=op_name,
                    duration_seconds=round(duration, 3),
                    status="success"
                )
                return result
            except Exception as e:
                duration = time.time() - start_time
                module_logger.error(
                    f"Failed: {op_name}",
                    operation=op_name,
                    duration_seconds=round(duration, 3),
                    status="error",
                    error_type=type(e).__name__,
                    error_message=str(e)
                )
                raise

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            start_time = time.time()
            try:
                module_logger.debug(f"Starting async: {op_name}")
                result = await func(*args, **kwargs)
                duration = time.time() - start_time
                module_logger.info(
                    f"Completed async: {op_name}",
                    operation=op_name,
                    duration_seconds=round(duration, 3),
                    status="success"
                )
                return result
            except Exception as e:
                duration = time.time() - start_time
                module_logger.error(
                    f"Failed async: {op_name}",
                    operation=op_name,
                    duration_seconds=round(duration, 3),
                    status="error",
                    error_type=type(e).__name__,
                    error_message=str(e)
                )
                raise

        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore
        return sync_wrapper  # type: ignore

    return decorator


@contextmanager
def log_context(operation: str, **metadata: Any) -> Any:
    """
    Context manager for operation-level logging.

    Simpler alternative to RequestContext for basic operation tracking.

    Args:
        operation: Operation name
        **metadata: Additional context fields

    Example:
        with log_context("data_processing", record_count=100):
            # Do work
            pass
    """
    module_logger = get_logger("operation")
    start_time = time.time()
    request_id = str(uuid.uuid4())

    module_logger.info(
        f"Operation started: {operation}",
        operation=operation,
        request_id=request_id,
        **metadata
    )

    try:
        yield request_id
        duration = time.time() - start_time
        module_logger.info(
            f"Operation completed: {operation}",
            operation=operation,
            request_id=request_id,
            duration_seconds=round(duration, 3),
            status="success",
            **metadata
        )
    except Exception as e:
        duration = time.time() - start_time
        module_logger.error(
            f"Operation failed: {operation}",
            operation=operation,
            request_id=request_id,
            duration_seconds=round(duration, 3),
            status="error",
            error_type=type(e).__name__,
            error_message=str(e),
            **metadata
        )
        raise
