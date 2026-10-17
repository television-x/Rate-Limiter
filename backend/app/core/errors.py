"""Typed domain errors for the rate limiter core.

These errors are raised by core components and caught/handled by the API layer.
They must not depend on FastAPI, Redis, or any outer layer.
"""


class RateLimiterError(Exception):
    """Base class for all rate limiter domain errors."""


# What happens if Redis is down?
class StorageUnavailable(RateLimiterError):
    """Raised when the storage backend is unreachable or timed out.

    The caller should apply the configured failure policy (open, closed,
    or local_fallback).
    """

    def __init__(self, message: str = "Storage backend is unavailable") -> None:
        super().__init__(message)
        self.message = message


class ContentionExhausted(RateLimiterError):
    """Raised when optimistic transaction retries are exhausted.

    This means the key is under extreme contention and the bounded
    retry count (e.g. 5 attempts) has been exceeded without a
    successful commit.
    """

    def __init__(
        self,
        key: str,
        attempts: int,
        message: str = "Transaction retry limit exhausted",
    ) -> None:
        super().__init__(message)
        self.key = key
        self.attempts = attempts
        self.message = message


class InvalidPolicy(RateLimiterError):
    """Raised when a rate-limit policy fails validation.

    This is a configuration error caught at load time, not a runtime
    user error.
    """

    def __init__(self, policy_id: str, reason: str) -> None:
        super().__init__(f"Invalid policy '{policy_id}': {reason}")
        self.policy_id = policy_id
        self.reason = reason


class InvalidIdentity(RateLimiterError):
    """Raised when a client identity is malformed.

    Examples: overlong identity string, control characters, empty
    after sanitization. This results in a 400 response at the API
    layer.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(f"Invalid identity: {reason}")
        self.reason = reason
