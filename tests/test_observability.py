from __future__ import annotations

from contextlib import contextmanager
import sys
from types import ModuleType

from src import observability


def _reset_observability(monkeypatch):
  monkeypatch.setattr(observability, "_CONFIGURED", False)
  monkeypatch.setattr(observability, "_TRACING_ENABLED", False)
  monkeypatch.setattr(observability, "_TRACER", None)


def test_setup_phoenix_tracing_registers_provider(monkeypatch):
  _reset_observability(monkeypatch)
  calls = []
  spans = []

  class FakeTracer:
    def start_as_current_span(self, name, attributes=None):
      @contextmanager
      def span():
        spans.append((name, attributes))
        yield

      return span()

  class FakeProvider:
    def get_tracer(self, name):
      calls.append({"get_tracer": name})
      return FakeTracer()

  def fake_register(**kwargs):
    calls.append(kwargs)
    return FakeProvider()

  phoenix = ModuleType("phoenix")
  phoenix.__path__ = []
  otel = ModuleType("phoenix.otel")
  otel.register = fake_register
  monkeypatch.setitem(sys.modules, "phoenix", phoenix)
  monkeypatch.setitem(sys.modules, "phoenix.otel", otel)
  monkeypatch.setenv("HANDA_PHOENIX_ENABLED", "1")
  monkeypatch.setenv("PHOENIX_COLLECTOR_ENDPOINT", "http://127.0.0.1:6007")
  monkeypatch.setenv("PHOENIX_PROJECT_NAME", "handa-test")

  assert observability.setup_phoenix_tracing() is True

  assert calls[0] == {
      "endpoint": "http://127.0.0.1:6007",
      "project_name": "handa-test",
      "protocol": "grpc",
      "batch": False,
      "auto_instrument": True,
      "verbose": False,
  }
  assert calls[1] == {"get_tracer": "handa"}

  with observability.trace_span("handa.test", {"session_id": "session-1", "skip": None}):
    pass

  assert spans == [("handa.test", {"session_id": "session-1"})]


def test_setup_phoenix_tracing_respects_disabled_env(monkeypatch):
  _reset_observability(monkeypatch)
  monkeypatch.setenv("HANDA_PHOENIX_ENABLED", "0")
  monkeypatch.setenv("PHOENIX_COLLECTOR_ENDPOINT", "http://127.0.0.1:6007")

  assert observability.setup_phoenix_tracing() is False
