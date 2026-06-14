from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from collections.abc import Callable
from typing import TypeVar

from google.genai.errors import APIError


RETRYABLE_RUN_ERROR_CODES = {429, 500, 502, 503, 504}
RATE_LIMIT_RUN_ERROR_CODES = {429}

# Shared policy for re-running a whole agent invocation on transient model
# errors. Used by agent_run_worker (child runs) and run_manager (web turns).
MAX_RUN_ATTEMPTS = 3
RUN_RETRY_BASE_DELAY_SEC = 2.0
RATE_LIMIT_RETRY_DELAY_SEC = 60.0

_T = TypeVar("_T")


def is_retryable_run_error(exc: Exception) -> bool:
  raw_code = getattr(exc, "code", None)
  if raw_code is None:
    code = None
  else:
    try:
      code = int(raw_code)
    except (TypeError, ValueError):
      code = None
  if isinstance(exc, APIError) and code in RETRYABLE_RUN_ERROR_CODES:
    return True
  if code in RETRYABLE_RUN_ERROR_CODES:
    return True

  message = str(exc).lower()
  return (
      "resource_exhausted" in message
      or "rate limit" in message
      or "quota" in message
      or "429" in message
  )


def is_rate_limit_run_error(exc: Exception) -> bool:
  raw_code = getattr(exc, "code", None)
  if raw_code is None:
    code = None
  else:
    try:
      code = int(raw_code)
    except (TypeError, ValueError):
      code = None
  if code in RATE_LIMIT_RUN_ERROR_CODES:
    return True

  message = str(exc).lower()
  return (
      "resource_exhausted" in message
      or "rate limit" in message
      or "quota" in message
      or "429" in message
  )


def retry_delay_for_error(exc: Exception, fallback_delay_sec: float) -> float:
  """Rate-limit errors wait a fixed cool-off; others use the caller's backoff."""
  if is_rate_limit_run_error(exc):
    return RATE_LIMIT_RETRY_DELAY_SEC
  return fallback_delay_sec


async def run_with_retries(
    attempt: Callable[[], Awaitable[_T]],
    *,
    should_retry: Callable[[], bool] = lambda: True,
    max_attempts: int = MAX_RUN_ATTEMPTS,
    base_delay_sec: float = RUN_RETRY_BASE_DELAY_SEC,
    on_retry: Callable[[int, float, Exception], None] | None = None,
) -> _T:
  """Run an async attempt with bounded backoff on transient model errors.

  Retries a failed attempt only when the error is transient
  (``is_retryable_run_error``), attempts remain, and ``should_retry()`` is True.
  Callers that stream side effects pass a "no output produced yet" guard as
  ``should_retry`` so a partially-streamed run is never re-run (which would
  duplicate visible output). Rate-limit errors wait
  ``RATE_LIMIT_RETRY_DELAY_SEC``; other transient errors use exponential backoff
  from ``base_delay_sec``. ``on_retry(attempt_no, delay_sec, exc)`` fires before
  each sleep so callers can trace the retry.
  """
  delay_sec = base_delay_sec
  for attempt_no in range(1, max_attempts + 1):
    try:
      return await attempt()
    except Exception as exc:  # noqa: BLE001 - re-raised below unless transient.
      if (
          attempt_no >= max_attempts
          or not is_retryable_run_error(exc)
          or not should_retry()
      ):
        raise
      retry_delay_sec = retry_delay_for_error(exc, delay_sec)
      if on_retry is not None:
        on_retry(attempt_no, retry_delay_sec, exc)
      await asyncio.sleep(retry_delay_sec)
      delay_sec *= 2
  raise AssertionError("run_with_retries: unreachable")  # pragma: no cover
