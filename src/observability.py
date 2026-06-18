from __future__ import annotations

from contextlib import nullcontext
import logging
import os
from typing import Any
from typing import ContextManager
from typing import Mapping

LOGGER = logging.getLogger(__name__)
DEFAULT_PHOENIX_COLLECTOR_ENDPOINT = "http://127.0.0.1:4317"
_CONFIGURED = False
_TRACING_ENABLED = False
_TRACER: Any = None


def setup_phoenix_tracing() -> bool:
  """Configure Phoenix/OpenTelemetry tracing whenever Phoenix is available."""
  global _CONFIGURED, _TRACING_ENABLED, _TRACER
  if _CONFIGURED:
    return _TRACING_ENABLED
  _CONFIGURED = True

  if not _phoenix_enabled():
    return False

  try:
    from phoenix.otel import register
  except Exception as exc:  # noqa: BLE001 - observability is optional.
    if _env_flag("HANDA_PHOENIX_ENABLED", default=False):
      LOGGER.warning("Phoenix tracing requested but unavailable: %s", exc)
    else:
      LOGGER.debug("Phoenix tracing unavailable: %s", exc)
    return False

  endpoint = os.getenv("PHOENIX_COLLECTOR_ENDPOINT") or DEFAULT_PHOENIX_COLLECTOR_ENDPOINT
  project_name = os.getenv("PHOENIX_PROJECT_NAME") or "handa"
  try:
    provider = register(
        endpoint=endpoint,
        project_name=project_name,
        protocol=_collector_protocol(endpoint),
        batch=_env_flag("HANDA_PHOENIX_BATCH", default=False),
        auto_instrument=_env_flag("HANDA_PHOENIX_AUTO_INSTRUMENT", default=True),
        verbose=False,
    )
  except Exception as exc:  # noqa: BLE001 - tracing must never break runs.
    LOGGER.warning("Phoenix tracing setup failed: %s", exc)
    return False

  _TRACER = provider.get_tracer("handa")
  _TRACING_ENABLED = True
  return True


def trace_span(
    name: str,
    attributes: Mapping[str, Any] | None = None,
) -> ContextManager[Any]:
  if not _TRACING_ENABLED:
    return nullcontext()
  try:
    tracer = _TRACER
    if tracer is None:
      from opentelemetry import trace
      tracer = trace.get_tracer("handa")
    return tracer.start_as_current_span(name, attributes=_span_attributes(attributes))
  except Exception as exc:  # noqa: BLE001 - tracing must never break runs.
    LOGGER.debug("Phoenix span skipped: %s", exc)
    return nullcontext()


def _phoenix_enabled() -> bool:
  value = os.getenv("HANDA_PHOENIX_ENABLED")
  if value is None:
    return True
  return value.strip().lower() not in {"", "0", "false", "no", "off"}


def _env_flag(name: str, *, default: bool) -> bool:
  value = os.getenv(name)
  if value is None:
    return default
  return value.strip().lower() not in {"", "0", "false", "no", "off"}


def _collector_protocol(endpoint: str | None) -> str | None:
  configured = os.getenv("PHOENIX_COLLECTOR_PROTOCOL", "").strip().lower()
  if configured in {"grpc", "http/protobuf"}:
    return configured
  if not endpoint:
    return None
  if endpoint.rstrip("/").endswith("/v1/traces"):
    return "http/protobuf"
  return "grpc"


def _span_attributes(attributes: Mapping[str, Any] | None) -> dict[str, Any]:
  if not attributes:
    return {}
  return {
      key: value
      for key, value in attributes.items()
      if value is not None and isinstance(value, (str, bool, int, float))
  }
