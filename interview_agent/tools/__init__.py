from .base import Tool, ToolContext, ToolRegistry
from .file_tools import build_file_tools
from .github_tool import build_github_tools
from .transcript_tool import build_transcript_tools


def default_registry() -> ToolRegistry:
    reg = ToolRegistry()
    for t in build_file_tools() + build_transcript_tools() + build_github_tools():
        reg.register(t)
    return reg


__all__ = ["Tool", "ToolContext", "ToolRegistry", "default_registry"]
