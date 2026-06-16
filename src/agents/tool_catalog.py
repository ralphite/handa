from __future__ import annotations


AGENT_TOOL_NAMES = frozenset(
    {
        "run_agent",
        "request_user_input",
        "agents_save_config",
        "agents_read_config",
        "agents_list_configs",
        "agents_start_run",
        "agents_get_run_status",
        "agents_read_run_result",
        "agents_read_run_log",
        "agents_list_run_artifacts",
        "agents_read_run_artifact",
        "artifacts_save_text",
        "artifacts_list",
        "artifacts_read",
        "browser_open",
        "browser_snapshot",
        "browser_click",
        "browser_type",
        "browser_keys",
        "browser_scroll",
        "browser_wait",
        "browser_screenshot",
        "browser_close",
        "skills_list",
        "skills_read",
        "files_list",
        "files_search",
        "files_read",
        "files_write",
        "files_replace",
        "commands_run",
        "tasks_start_background",
        "tasks_get_status",
        "tasks_list",
        "tasks_read_log",
        "tasks_cancel",
        "notifications_get",
        "progress_update",
        "notes_add",
    }
)


def known_agent_tool_names() -> frozenset[str]:
  return AGENT_TOOL_NAMES
