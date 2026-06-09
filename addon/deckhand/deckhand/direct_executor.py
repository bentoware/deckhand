from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from time import perf_counter
from typing import Any


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    result: Any | None
    error: str | None
    duration_ms: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "result": self.result,
            "error": self.error,
            "durationMs": self.duration_ms,
        }


class DirectExecutor:
    _ALLOWED_PREFIXES = ("anki_",)

    def __init__(self) -> None:
        self._handlers: dict[str, Callable[[dict[str, Any]], Any]] = {}

    def register(self, name: str, handler: Callable[[dict[str, Any]], Any]) -> None:
        if not name.startswith(self._ALLOWED_PREFIXES):
            raise ValueError("tool names must use a Deckhand catalog namespace")
        self._handlers[name] = handler

    def unregister(self, name: str) -> None:
        self._handlers.pop(name, None)

    def tools(self) -> list[str]:
        return sorted(self._handlers)

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> ToolResult:
        started = perf_counter()
        if name not in self._handlers:
            return ToolResult(False, None, "tool_not_found", 0)
        try:
            result = self._handlers[name](arguments or {})
            return ToolResult(True, result, None, _elapsed_ms(started))
        except BaseException as exc:  # noqa: BLE001 - keep dev/tool failures from unwinding Anki
            return ToolResult(False, None, f"execution_failed: {exc}", _elapsed_ms(started))


def _elapsed_ms(started: float) -> int:
    return max(0, int((perf_counter() - started) * 1000))
