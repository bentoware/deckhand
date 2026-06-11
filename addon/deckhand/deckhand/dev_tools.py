from __future__ import annotations

import builtins
import builtins as builtins_module
import json
import os
import posix
import sys
from pathlib import Path
from typing import Any

MAX_RESULT_CHARS = 4000
DEFAULT_INLINE_LIMIT_BYTES = 12_000
MAX_INLINE_LIMIT_BYTES = 64_000
RESULT_PREVIEW_CHARS = 4_000


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


def run_python_snippet(
    snippet: str,
    *,
    result_file_path: str | None = None,
    result_format: str = "json",
    inline_limit_bytes: int | object = DEFAULT_INLINE_LIMIT_BYTES,
) -> dict[str, object]:
    scope = _anki_snippet_globals()
    try:
        exec(compile(snippet, "<deckhand-dev-snippet>", "exec"), scope, scope)
    except BaseException as exc:  # noqa: BLE001 - normalize snippet exits/crashes into tool errors
        raise DevToolError(_format_snippet_failure(exc)) from exc
    return result_envelope(
        scope.get("result"),
        result_file_path=result_file_path,
        result_format=result_format,
        inline_limit_bytes=inline_limit_bytes,
    )


def result_envelope(
    value: Any,
    *,
    result_file_path: str | None = None,
    result_format: str = "json",
    inline_limit_bytes: int | object = DEFAULT_INLINE_LIMIT_BYTES,
) -> dict[str, object]:
    fmt = _normalize_result_format(result_format)
    inline_limit = _normalize_inline_limit(inline_limit_bytes)
    json_safe = json_safe_result(value)
    serialized = _serialize_result(json_safe, fmt)
    result_bytes = len(serialized.encode("utf-8"))
    preview = _preview(serialized)
    artifact = None

    if result_file_path:
        path = Path(result_file_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        if fmt == "json":
            path.write_text(serialized + "\n", encoding="utf-8")
        else:
            path.write_text(serialized, encoding="utf-8")
        artifact = {
            "path": str(path),
            "bytes": path.stat().st_size,
            "format": fmt,
        }
        return {
            "result": None,
            "resultInline": False,
            "resultTruncated": False,
            "resultOmitted": False,
            "resultBytes": result_bytes,
            "resultPreview": preview,
            "artifact": artifact,
        }

    if result_bytes <= inline_limit:
        return {
            "result": redact_value(json_safe),
            "resultInline": True,
            "resultTruncated": False,
            "resultOmitted": False,
            "resultBytes": result_bytes,
            "resultPreview": None,
            "artifact": None,
        }

    return {
        "result": None,
        "resultInline": False,
        "resultTruncated": False,
        "resultOmitted": True,
        "resultBytes": result_bytes,
        "resultPreview": preview,
        "artifact": None,
        "message": "Result omitted from inline response; rerun with resultFilePath to write full output.",
    }


def json_safe_result(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_safe_result(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe_result(item) for item in value]
    if isinstance(value, tuple):
        return [json_safe_result(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    try:
        json.dumps(value)
        return value
    except TypeError:
        return repr(value)


def redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return redact_secrets(value)
    return value


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


def _serialize_result(value: Any, fmt: str) -> str:
    if fmt == "json":
        return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True)


def _preview(serialized: str) -> str:
    return redact_secrets(truncate(serialized, RESULT_PREVIEW_CHARS))


def _normalize_result_format(value: str | object) -> str:
    fmt = str(value or "json").lower()
    if fmt not in {"json", "text"}:
        raise DevToolError("unsupported_result_format")
    return fmt


def _normalize_inline_limit(value: int | object) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = DEFAULT_INLINE_LIMIT_BYTES
    return max(0, min(MAX_INLINE_LIMIT_BYTES, parsed))


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
