from __future__ import annotations

from dataclasses import dataclass

from fastapi import Request

from ..contract.services import HandaServices
from .settings import WebSettings
from .sqlite import WebDatabase


@dataclass(frozen=True)
class WebApiContext:
  settings: WebSettings
  services: HandaServices
  db: WebDatabase


def get_context(request: Request) -> WebApiContext:
  return request.app.state.web_context
