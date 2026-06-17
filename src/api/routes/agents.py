from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Request

from ...contract.introspection import read_tool_catalog
from ...contract.product import get_agent_definition
from ...contract.product import INSTRUCTION_SECTIONS
from ...contract.product import list_agent_definitions
from ...contract.product import list_model_config_options
from ...contract.product import list_skills
from ..context import get_context
from ..context_usage_breakdown import build_static_context_usage_breakdown
from ..schemas import AgentCatalog
from ..schemas import AgentContextUsageSummary
from ..schemas import AgentDefinitionSummary


router = APIRouter(prefix="/api/agents")


@router.get("", response_model=list[AgentDefinitionSummary])
def list_agents() -> list[dict[str, str]]:
  return [definition.model_dump() for definition in list_agent_definitions()]


@router.get("/catalog", response_model=AgentCatalog)
def get_agent_catalog(request: Request) -> dict[str, Any]:
  """Static reference data for rendering agent configs.

  Resolves the keys an AgentConfig references — tools, instruction sections,
  skills, model configs — into their definitions. Tool texts come from the
  introspection export (refreshed at Web startup), so a missing or stale
  export degrades to missing tool entries rather than failing.
  """
  ctx = get_context(request)
  return {
      "tools": [
          {
              "name": item["name"],
              "namespace": item["namespace"],
              "definition": item["text"],
          }
          for item in read_tool_catalog(ctx.services.storage_root)
      ],
      "instruction_sections": [
          {
              "name": section.name,
              "title": section.title,
              "template": section.template,
          }
          for section in INSTRUCTION_SECTIONS.values()
      ],
      "skills": [
          {
              "name": skill["name"],
              "skill_name": skill["skill_name"],
              "description": skill["description"],
              "source": skill["source"],
          }
          for skill in list_skills()["skills"]
      ],
      "agents": [definition.model_dump() for definition in list_agent_definitions()],
      "model_configs": [
          {
              "id": option.id,
              "label": option.label,
              "description": option.description,
              "context_window": option.context_window,
          }
          for option in list_model_config_options()
      ],
  }


@router.get("/{agent_id}/context-usage", response_model=AgentContextUsageSummary)
def get_agent_context_usage(
    agent_id: str,
    request: Request,
    project_id: str | None = None,
) -> dict[str, Any]:
  """Static context preview for a fresh session: instruction, tools, skills.

  Backs the new-chat context ring, where no runtime token usage exists yet.
  """
  try:
    definition = get_agent_definition(agent_id)
  except ValueError as exc:
    raise HTTPException(status_code=404, detail=str(exc)) from exc
  ctx = get_context(request)
  project = ctx.db.get_project(project_id) if project_id else None
  project_root = project["root_path"] if project else None
  breakdown = build_static_context_usage_breakdown(
      agent_id=definition.id,
      project_root=project_root,
  )
  return {
      "agent_id": definition.id,
      "agent_runtime": definition.runtime,
      "project_id": project["id"] if project else None,
      "total_token_count": sum(item["token_count"] for item in breakdown),
      "breakdown": breakdown,
  }
