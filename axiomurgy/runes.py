"""Rune registry, handlers, and MCP client."""

from . import legacy as _legacy

RuneHandler = _legacy.RuneHandler
RuneRegistry = _legacy.RuneRegistry
REGISTRY = _legacy.REGISTRY
MCPClient = _legacy.MCPClient
coerce_text = _legacy.coerce_text
target_label = _legacy.target_label


def __getattr__(name: str):
    if name.startswith("rune_"):
        return getattr(_legacy, name)
    raise AttributeError(name)


__all__ = [
    "RuneHandler",
    "RuneRegistry",
    "REGISTRY",
    "MCPClient",
    "coerce_text",
    "target_label",
]
