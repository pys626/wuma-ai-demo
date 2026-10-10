"""An operation-wide deadline shared with model calls, independent of UI and secrets."""
from contextlib import contextmanager
from contextvars import ContextVar
import time


deadline = ContextVar("wuma_ai_deadline", default=None)
REQUEST_SECONDS = 45.0
OPERATION_SECONDS = 120.0


def remaining():
    limit = deadline.get()
    return max(0.0, limit - time.monotonic()) if limit is not None else REQUEST_SECONDS


@contextmanager
def request_deadline(limit):
    token = deadline.set(limit)
    try:
        yield
    finally:
        deadline.reset(token)
