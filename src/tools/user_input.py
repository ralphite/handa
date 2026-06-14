"""Compatibility shim: the user-input contract moved to src.user_input.

The pending-state key, question/answer validation, and response shapes are
part of the web↔runtime contract (the worker persists pending requests, the
Web API validates submissions), so they live outside the tools package.
"""
from __future__ import annotations

from ..contract.user_input import build_pending_request
from ..contract.user_input import cancelled_response
from ..contract.user_input import new_request_id
from ..contract.user_input import PENDING_USER_INPUT_STATE_KEY
from ..contract.user_input import USER_INPUT_TOOL_NAME
from ..contract.user_input import UserInputOption
from ..contract.user_input import UserInputQuestion
from ..contract.user_input import validate_answers
from ..contract.user_input import validate_questions

__all__ = [
    "build_pending_request",
    "cancelled_response",
    "new_request_id",
    "PENDING_USER_INPUT_STATE_KEY",
    "USER_INPUT_TOOL_NAME",
    "UserInputOption",
    "UserInputQuestion",
    "validate_answers",
    "validate_questions",
]
