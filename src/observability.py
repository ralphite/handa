from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

_PHOENIX_INITIALIZED = False


def _is_enabled() -> bool:
  # Enabled by default; disable explicitly with a falsey HANDA_PHOENIX_ENABLED.
  return os.getenv("HANDA_PHOENIX_ENABLED", "1").strip().lower() not in {
      "0",
      "false",
      "no",
      "off",
      "",
  }


def setup_phoenix_tracing() -> bool:
  """Wire Google ADK and LangChain/LangGraph traces to a Phoenix collector.

  Enabled by default; opt out with HANDA_PHOENIX_ENABLED=0. Safe to call multiple
  times across entry points; instrumentation is applied at most once per process.
  Any failure (missing obs deps, collector setup error) is swallowed so tracing
  never breaks an agent run.
  """
  global _PHOENIX_INITIALIZED
  if _PHOENIX_INITIALIZED or not _is_enabled():
    return False

  try:
    from openinference.instrumentation.google_adk import GoogleADKInstrumentor
    from phoenix.otel import register

    tracer_provider = register(
        project_name=os.getenv("PHOENIX_PROJECT_NAME", "handa"),
        auto_instrument=False,
        batch=True,
        verbose=False,
    )
    GoogleADKInstrumentor().instrument(tracer_provider=tracer_provider)
    _instrument_langchain(tracer_provider)
  except Exception:  # noqa: BLE001 - observability must never break a run.
    logger.warning("Phoenix tracing setup failed; continuing without it.", exc_info=True)
    return False

  _PHOENIX_INITIALIZED = True
  return True


def _instrument_langchain(tracer_provider) -> None:
  """Trace the LangGraph runtime too; without this only ADK sessions reach Phoenix."""
  try:
    from openinference.instrumentation.langchain import LangChainInstrumentor

    LangChainInstrumentor().instrument(tracer_provider=tracer_provider)
  except ImportError:
    logger.info("openinference-instrumentation-langchain not installed; LangGraph runs untraced.")
  except Exception:  # noqa: BLE001 - observability must never break a run.
    logger.warning("LangChain instrumentation failed; continuing without it.", exc_info=True)
