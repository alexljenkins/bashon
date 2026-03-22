"""Bashon exceptions."""

from __future__ import annotations


class BashonError(Exception):
    """Base error for Bashon."""


class AliasError(BashonError):
    """Raised when alias operations fail."""


class CommandNotFoundError(BashonError):
    """Raised when a command or target cannot be resolved."""


class ParseError(BashonError):
    """Raised when CLI input cannot be parsed."""

    def __init__(
        self,
        message: str,
        *,
        parameter: str | None = None,
        expected_type: str | None = None,
        received_value: object | None = None,
    ) -> None:
        super().__init__(message)
        self.parameter = parameter
        self.expected_type = expected_type
        self.received_value = received_value


class SerializationError(BashonError):
    """Raised when a return value cannot be serialized for agent mode."""


class UnsupportedCallableError(BashonError):
    """Raised when a decorated callable cannot be safely exposed."""
