"""
Custom exception classes for the Harness application.

All exceptions inherit from HarnessError and support:
- Error codes for programmatic handling
- Detailed error messages
- Additional metadata in details dict
- to_dict() for JSON serialization
"""

from typing import Any, Dict, Optional


class HarnessError(Exception):
    """
    Base exception class for all Harness-specific errors.

    Attributes:
        message: Human-readable error message
        error_code: Machine-readable error code (e.g., "CONFIG_001")
        details: Additional context about the error
    """

    def __init__(
        self,
        message: str,
        error_code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        self.message = message
        self.error_code = error_code or "HARNESS_ERROR"
        self.details = details or {}
        super().__init__(self.message)

    def to_dict(self) -> Dict[str, Any]:
        """
        Convert exception to dictionary for JSON serialization.

        Returns:
            Dictionary containing error information
        """
        return {
            "error_type": self.__class__.__name__,
            "error_code": self.error_code,
            "message": self.message,
            "details": self.details
        }

    def __str__(self) -> str:
        """String representation including error code."""
        if self.details:
            return f"[{self.error_code}] {self.message} - Details: {self.details}"
        return f"[{self.error_code}] {self.message}"


class ConfigError(HarnessError):
    """
    Exception raised for configuration-related errors.

    Error code format: CONFIG_XXX
    """

    def __init__(
        self,
        message: str,
        error_code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        super().__init__(
            message,
            error_code or "CONFIG_ERROR",
            details
        )


class TdxConnectionError(HarnessError):
    """
    Exception raised for TDX connection-related errors.

    Error code format: TDX_CONN_XXX
    """

    def __init__(
        self,
        message: str,
        error_code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        super().__init__(
            message,
            error_code or "TDX_CONN_ERROR",
            details
        )


class GlmApiError(HarnessError):
    """
    Exception raised for GLM API-related errors.

    Error code format: GLM_API_XXX
    """

    def __init__(
        self,
        message: str,
        error_code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        super().__init__(
            message,
            error_code or "GLM_API_ERROR",
            details
        )


class SkillError(HarnessError):
    """
    Exception raised for skill-related errors.

    Error code format: SKILL_XXX
    """

    def __init__(
        self,
        message: str,
        error_code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        super().__init__(
            message,
            error_code or "SKILL_ERROR",
            details
        )


class ValidationError(HarnessError):
    """
    Exception raised for data validation errors.

    Error code format: VALIDATION_XXX
    """

    def __init__(
        self,
        message: str,
        error_code: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None
    ):
        super().__init__(
            message,
            error_code or "VALIDATION_ERROR",
            details
        )
