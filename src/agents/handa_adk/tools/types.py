from __future__ import annotations

from collections.abc import Awaitable
from collections.abc import Callable
from typing import Any
from typing import TypedDict


FunctionToolPayload = dict[str, Any]
FunctionToolCallable = Callable[..., FunctionToolPayload | Awaitable[FunctionToolPayload]]


class FunctionToolError(TypedDict):
  type: str
  message: str
  tool: str


class FunctionToolResult(TypedDict):
  ok: bool


class FunctionToolFailureResult(FunctionToolResult):
  error: FunctionToolError
