"""Errors the application service raises; the API maps them to HTTP statuses."""


class NotFoundError(LookupError):
    pass


class ValidationError(ValueError):
    pass


class ConflictError(RuntimeError):
    """An operation conflicts with the current resource state."""
