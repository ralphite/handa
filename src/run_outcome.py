from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RunOutcome:
  """Result of one agent invocation, across agent runtimes.

  Either the agent finished with `final_text`, or it paused on a
  `request_user_input` call and `pending_user_input` carries the payload the
  Web layer needs to render the form and resume later.
  """

  final_text: str = ""
  pending_user_input: dict[str, Any] | None = None
  goal_status: str | None = None
  goal_verdict: dict[str, Any] | None = None
