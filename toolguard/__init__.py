"""toolguard — a provenance-aware, LLM-guided guard for AI agent tool calls.

Quick start (rules-only, no API key):

    from toolguard import Guard, default_workspace_config
    from toolguard.trace import load_trace

    meta, messages = load_trace("trace.json")
    guard = Guard(default_workspace_config())
    result = guard.decide(messages, task=meta["user_task_id"])
    print(result.decision, "-", result.reason)

Add an LLM to reach the guided decision:

    from toolguard.llm import GeminiProvider
    guard = Guard(default_workspace_config(), provider=GeminiProvider())
"""
from .config import GuardConfig, default_workspace_config
from .guard import Guard
from .types import Decision, Evidence, GuardResult, Message, ToolCall

__version__ = "0.2.0"
__all__ = [
    "Guard", "GuardConfig", "default_workspace_config",
    "Decision", "Evidence", "GuardResult", "Message", "ToolCall",
    "__version__",
]
