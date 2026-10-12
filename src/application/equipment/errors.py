class CostSystemError(Exception):
    """A call to the external cost system failed."""


class CostSystemUnavailableError(CostSystemError):
    """The system is down, slow or answered 5xx/429: the call may be repeated."""


class CostSystemRejectedError(CostSystemError):
    """The system rejected the data (4xx or a malformed answer): repeating won't help."""
