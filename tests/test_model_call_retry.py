from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from src.agents import native_runner
from src.agents.native_runner import MODEL_RETRY_MAX_DELAY_SEC
from src.agents.native_runner import MODEL_TRANSIENT_RETRY_ATTEMPTS
from src.agents.native_runner import generate_model_response


class _FakeAPIError(Exception):
  """Stands in for google.genai APIError: classified by its ``code``."""

  def __init__(self, code: int, message: str = "") -> None:
    super().__init__(message or f"{code} error")
    self.code = code


class _FakeModels:
  def __init__(self, script: list[object]) -> None:
    # Each entry is either an exception to raise or a value to return.
    self._script = list(script)
    self.calls = 0

  async def generate_content(self, *, model, contents, config):
    self.calls += 1
    item = self._script.pop(0)
    if isinstance(item, Exception):
      raise item
    return item


def _client(script: list[object]) -> SimpleNamespace:
  models = _FakeModels(script)
  return SimpleNamespace(aio=SimpleNamespace(models=models), _models=models)


def _patch_sleep(monkeypatch) -> list[float]:
  slept: list[float] = []

  async def _fake_sleep(delay: float) -> None:
    slept.append(delay)

  monkeypatch.setattr(native_runner.asyncio, "sleep", _fake_sleep)
  return slept


async def _call(client, on_retry=None):
  return await generate_model_response(
      client=client,
      model="gemini-test",
      contents=[],
      config=SimpleNamespace(),
      on_retry=on_retry,
  )


def test_rate_limit_retries_until_success(monkeypatch):
  slept = _patch_sleep(monkeypatch)
  client = _client([_FakeAPIError(429), _FakeAPIError(429), "ok"])
  retries: list[tuple[int, float]] = []

  async def on_retry(attempt_no, delay_sec, exc):
    retries.append((attempt_no, delay_sec))

  result = asyncio.run(_call(client, on_retry=on_retry))

  assert result == "ok"
  assert client._models.calls == 3
  # Two retries fired, with exponential backoff (1s then 2s).
  assert [d for _, d in retries] == [1.0, 2.0]
  assert slept == [1.0, 2.0]


def test_rate_limit_retries_far_past_transient_cap(monkeypatch):
  # Rate-limit errors must NOT be bounded by the transient-error cap: keep
  # retrying well past it, then succeed.
  _patch_sleep(monkeypatch)
  failures = [_FakeAPIError(429) for _ in range(MODEL_TRANSIENT_RETRY_ATTEMPTS * 3)]
  client = _client([*failures, "ok"])

  result = asyncio.run(_call(client))

  assert result == "ok"
  assert client._models.calls == len(failures) + 1


def test_backoff_is_capped_at_five_minutes(monkeypatch):
  slept = _patch_sleep(monkeypatch)
  # Enough rate-limit errors that exponential growth would blow past the cap.
  failures = [_FakeAPIError(429) for _ in range(20)]
  client = _client([*failures, "ok"])

  asyncio.run(_call(client))

  assert max(slept) == MODEL_RETRY_MAX_DELAY_SEC == 300.0
  # Once capped, every subsequent wait stays at the ceiling.
  assert slept[-1] == MODEL_RETRY_MAX_DELAY_SEC


def test_transient_error_is_bounded(monkeypatch):
  _patch_sleep(monkeypatch)
  # A non-rate-limit transient error (503) should retry a bounded number of
  # times and then surface, so a real outage fails fast.
  failures = [_FakeAPIError(503) for _ in range(MODEL_TRANSIENT_RETRY_ATTEMPTS)]
  client = _client([*failures, "ok"])

  with pytest.raises(_FakeAPIError) as excinfo:
    asyncio.run(_call(client))

  assert excinfo.value.code == 503
  assert client._models.calls == MODEL_TRANSIENT_RETRY_ATTEMPTS


def test_non_retryable_error_raises_immediately(monkeypatch):
  _patch_sleep(monkeypatch)
  client = _client([_FakeAPIError(400, "bad request")])

  with pytest.raises(_FakeAPIError) as excinfo:
    asyncio.run(_call(client))

  assert excinfo.value.code == 400
  assert client._models.calls == 1


def test_cancellation_propagates(monkeypatch):
  _patch_sleep(monkeypatch)

  async def scenario():
    async def on_retry(attempt_no, delay_sec, exc):
      raise asyncio.CancelledError()

    client = _client([_FakeAPIError(429), "ok"])
    with pytest.raises(asyncio.CancelledError):
      await _call(client, on_retry=on_retry)

  asyncio.run(scenario())
