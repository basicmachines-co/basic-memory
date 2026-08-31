"""LLM runner abstractions for answer generation, judging, and tool-use agents."""

from basic_memory_benchmarks.llm.runners import (
    ClaudeCLIRunner,
    LLMResult,
    LLMRunner,
    LLMRunnerError,
    OpenAICompatRunner,
    create_runner,
)
from basic_memory_benchmarks.llm.tool_agent import (
    AgentTurn,
    AssistantTurn,
    OpenAICompatToolAgent,
    ScriptedToolAgent,
    ToolAgentModel,
    ToolCall,
    ToolDef,
    ToolReturn,
    TranscriptItem,
    UserMessage,
    create_tool_agent_model,
    substitute_placeholders,
)

__all__ = [
    "AgentTurn",
    "AssistantTurn",
    "ClaudeCLIRunner",
    "LLMResult",
    "LLMRunner",
    "LLMRunnerError",
    "OpenAICompatRunner",
    "OpenAICompatToolAgent",
    "ScriptedToolAgent",
    "ToolAgentModel",
    "ToolCall",
    "ToolDef",
    "ToolReturn",
    "TranscriptItem",
    "UserMessage",
    "create_runner",
    "create_tool_agent_model",
    "substitute_placeholders",
]
