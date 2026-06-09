from __future__ import annotations

import builtins
import builtins as builtins_module
import json
import os
import posix
import sys
from typing import Any

MAX_RESULT_CHARS = 4000


class DevToolError(RuntimeError):
    pass


class GuardedModuleProxy:
    def __init__(self, module: object, blocked_members: dict[str, str]) -> None:
        self._module = module
        self._blocked_members = blocked_members

    def __getattr__(self, name: str) -> object:
        if name in self._blocked_members:
            return _blocked_callable(self._blocked_members[name])
        return getattr(self._module, name)


def run_python_snippet(snippet: str) -> dict[str, object]:
    scope = _anki_snippet_globals()
    try:
        exec(compile(snippet, "<deckhand-dev-snippet>", "exec"), scope, scope)
    except BaseException as exc:  # noqa: BLE001 - normalize snippet exits/crashes into tool errors
        raise DevToolError(_format_snippet_failure(exc)) from exc
    return {
        "result": sanitize_result(scope.get("result")),
    }


def sanitize_result(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): sanitize_result(item) for key, item in value.items()}
    if isinstance(value, list):
        return [sanitize_result(item) for item in value[:100]]
    if isinstance(value, tuple):
        return [sanitize_result(item) for item in value[:100]]
    if isinstance(value, str):
        return redact_secrets(truncate(value))
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    try:
        return redact_secrets(truncate(json.dumps(value, default=repr)))
    except TypeError:
        return redact_secrets(truncate(repr(value)))


def truncate(value: str, limit: int = MAX_RESULT_CHARS) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "...[truncated]"


def redact_secrets(value: str) -> str:
    redacted = value
    for marker in ["token", "secret", "password", "api_key"]:
        if marker in redacted.lower():
            redacted = "[redacted]"
    return redacted


def _anki_snippet_globals() -> dict[str, object]:
    from aqt import mw
    import aqt

    guarded_sys = GuardedModuleProxy(sys, {"exit": "sys.exit"})
    guarded_os = GuardedModuleProxy(os, {"_exit": "os._exit", "abort": "os.abort"})
    guarded_posix = GuardedModuleProxy(posix, {"_exit": "posix._exit"})
    guarded_modules = {
        "sys": guarded_sys,
        "os": guarded_os,
        "posix": guarded_posix,
    }

    guarded_builtins = dict(vars(builtins_module))
    guarded_builtins["exit"] = _blocked_callable("exit")
    guarded_builtins["quit"] = _blocked_callable("quit")
    guarded_builtins["__import__"] = _guarded_import(guarded_modules)

    return {
        "__builtins__": guarded_builtins,
        "aqt": aqt,
        "os": guarded_os,
        "mw": mw,
        "result": None,
        "sys": guarded_sys,
    }


def _blocked_callable(name: str) -> Any:
    def blocked(*_args: Any, **_kwargs: Any) -> None:
        raise DevToolError(f"snippet_forbidden_operation:{name}")

    return blocked


def _guarded_import(guarded_modules: dict[str, object]) -> Any:
    original_import = builtins_module.__import__

    def guarded_import(name: str, globals: dict[str, object] | None = None, locals: dict[str, object] | None = None, fromlist: tuple[str, ...] = (), level: int = 0) -> object:
        module = original_import(name, globals, locals, fromlist, level)
        if level == 0 and name in guarded_modules:
            return guarded_modules[name]
        return module

    return guarded_import


def _format_snippet_failure(exc: BaseException) -> str:
    name = type(exc).__name__
    message = str(exc).strip()
    if isinstance(exc, DevToolError):
        return message or name
    if message:
        return f"snippet_failed:{name}: {message}"
    return f"snippet_failed:{name}"
