"""Guard configuration and ready-made profiles."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class GuardConfig:
    """Everything the guard needs that is domain-specific.

    write_tools     : tools that change the environment (only these are judged).
    dest_params     : tool -> the parameter holding destination values
                      (e.g. send_email -> recipients). Tools not listed have no
                      destination parameter, so source tracking is not applicable.
    destructive_tools: tools whose effect is irreversible (delete, overwrite...).
    plans           : task id -> set of tool names that the task's reference plan
                      legitimately uses. A task mapped to an empty set means the
                      plan is empty (undecidable by the plan rule).
    org_domains     : email domains considered internal; a destination outside
                      them is flagged external. Empty => external check disabled.
    """
    write_tools: set[str] = field(default_factory=set)
    dest_params: dict[str, str] = field(default_factory=dict)
    destructive_tools: set[str] = field(default_factory=set)
    plans: dict[str, set[str]] = field(default_factory=dict)
    org_domains: set[str] = field(default_factory=set)

    def plan_for(self, task: str) -> Optional[set[str]]:
        return self.plans.get(task)


def default_workspace_config() -> GuardConfig:
    """A profile for AgentDojo-style workspace traces.

    Tool names match the workspace suite; adapt or replace for your own agent.
    Plans are illustrative reference plans keyed by task id.
    """
    return GuardConfig(
        write_tools={"send_email", "delete_email", "delete_file", "create_calendar_event"},
        dest_params={"send_email": "recipients", "create_calendar_event": "participants"},
        destructive_tools={"delete_email", "delete_file"},
        plans={
            "user_task_0": {"search_calendar_events"},
            "task_send": {"send_email"},
            "task_delete_file": {"delete_file"},
            "task_create_event": {"create_calendar_event"},
        },
        org_domains=set(),  # set e.g. {"acme.example"} to enable the external flag
    )
