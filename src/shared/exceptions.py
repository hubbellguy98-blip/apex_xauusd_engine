"""Project-specific exception types."""


class ApexEngineError(Exception):
    """Base exception for engine failures."""


class ValidationError(ApexEngineError):
    """Raised when domain invariants are violated."""


class InfrastructureError(ApexEngineError):
    """Raised when an external transport or storage layer fails."""


class StateCorruptionError(ApexEngineError):
    """Raised when state transitions become unsafe."""
