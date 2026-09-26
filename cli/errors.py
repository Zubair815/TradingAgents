"""Concise operational failures without dumping provider response payloads."""

from tradingagents.llm_clients.response_validation import IncompleteResponseError


def describe_run_error(exc: Exception) -> str | None:
    """Describe known operational errors; let unexpected bugs retain tracebacks."""
    if isinstance(exc, IncompleteResponseError):
        return str(exc)
    status = getattr(exc, "status_code", None)
    if status == 402:
        return (
            "The provider rejected the request because of a credit or in-flight budget limit "
            "(HTTP 402). Allow outstanding requests to settle, then check the provider's "
            "credit balance before retrying."
        )
    if status in (401, 403):
        return f"The provider denied access (HTTP {status}). Check the API key and model permissions."
    if status == 429:
        return "The provider's rate limit was reached (HTTP 429). Wait before retrying."
    if isinstance(status, int) and status >= 500:
        return f"The provider is temporarily unavailable (HTTP {status}). Retry later."
    return None
