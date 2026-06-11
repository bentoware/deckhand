from __future__ import annotations

import json
import os
import platform
import secrets
import signal
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from . import settings


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = settings.DEFAULT_COMPANION_PORT
EXPECTED_SERVICE = "deckhand-anki-companion"
SERVER_BINARY = "deckhand-server.exe" if platform.system().lower() == "windows" else "deckhand-server"

_process: subprocess.Popen | None = None
_started_pid: int | None = None
_companion_token: str | None = None


def companion_host() -> str:
    return os.environ.get("DECKHAND_COMPANION_HOST", DEFAULT_HOST)


def companion_port() -> int:
    raw = os.environ.get("DECKHAND_COMPANION_PORT")
    if raw is None:
        return settings.companion_port()
    try:
        port = int(raw)
    except ValueError:
        return settings.companion_port()
    return port if 1 <= port <= 65535 else settings.companion_port()


def companion_url(host: str | None = None, port: int | None = None) -> str:
    host = host or companion_host()
    port = port or companion_port()
    return os.environ.get("DECKHAND_COMPANION_URL", f"http://{host}:{port}")


def companion_bind(host: str | None = None, port: int | None = None) -> str:
    return f"{host or companion_host()}:{port or companion_port()}"


def companion_token() -> str:
    global _companion_token
    configured = os.environ.get("DECKHAND_COMPANION_TOKEN")
    if configured:
        _companion_token = configured
        return configured
    if settings.require_mcp_token():
        # Client configs embed this token, so it must survive Anki restarts.
        _companion_token = settings.persistent_token()
        os.environ["DECKHAND_COMPANION_TOKEN"] = _companion_token
        return _companion_token
    if _companion_token is None:
        _companion_token = secrets.token_urlsafe(32)
        os.environ["DECKHAND_COMPANION_TOKEN"] = _companion_token
    return _companion_token


def bundled_server_path() -> Path:
    configured = os.environ.get("DECKHAND_COMPANION_BINARY")
    if configured:
        return Path(configured).expanduser()
    package_root = Path(__file__).resolve().parents[1]
    return package_root / "bin" / platform_tag() / SERVER_BINARY


def platform_tag(system: str | None = None, machine: str | None = None) -> str:
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    machine = {"amd64": "x86_64", "x64": "x86_64", "arm64": "aarch64"}.get(machine, machine)
    system = {"darwin": "macos"}.get(system, system)
    return f"{system}-{machine}"


def ensure_running(logger=None, *, force: bool = False) -> dict[str, Any]:
    if os.environ.get("DECKHAND_COMPANION_DISABLED") == "1":
        return _status("disabled", "companion autostart disabled")
    if not force and not settings.companion_autostart():
        current = health_status()
        if current["healthy"] and current.get("compatible", True):
            return _status(
                "running",
                "companion already healthy",
                owned=_started_pid is not None,
                pid=_started_pid,
                health=current,
            )
        return _status("disabled", "companion autostart disabled in settings", health=current)

    current = health_status()
    if current["healthy"] and current.get("compatible", True):
        return _status(
            "running",
            "companion already healthy",
            owned=_started_pid is not None,
            pid=_started_pid,
            health=current,
        )
    if current["healthy"] and not current.get("compatible", True):
        stopped = stop_recorded_companion(logger=logger)
        current = health_status()
        if current["healthy"] and not current.get("compatible", True):
            return _status(
                "stale",
                "companion is reachable but does not match this add-on",
                pid=read_pid_file(),
                health=current,
            )

    binary = bundled_server_path()
    if not binary.exists():
        return _status("missing", f"companion binary not found: {binary}", binary=binary)

    process = start_companion(binary, logger=logger)
    deadline = time.time() + float(os.environ.get("DECKHAND_COMPANION_START_TIMEOUT_SECONDS", "3"))
    health = health_status()
    while not health["healthy"] and time.time() < deadline:
        time.sleep(0.1)
        health = health_status()
    state = "running" if health["healthy"] else "starting"
    detail = "companion started by Anki" if health["healthy"] else "companion process started; health check pending"
    return _status(state, detail, owned=True, pid=process.pid, binary=binary, health=health)


def start_companion(binary: Path, logger=None) -> subprocess.Popen:
    global _process, _started_pid
    log_dir = Path(os.environ.get("DECKHAND_COMPANION_LOG_DIR", str(default_log_dir()))).expanduser()
    log_dir.mkdir(parents=True, exist_ok=True)
    stdout = (log_dir / "companion.stdout.log").open("ab")
    stderr = (log_dir / "companion.stderr.log").open("ab")
    command = [str(binary), "serve", "--bind", companion_bind()]
    token = companion_token()
    env = {
        **os.environ,
        "DECKHAND_COMPANION_LOG": str(log_dir / "companion.trace.log"),
        "DECKHAND_COMPANION_TOKEN": token,
        "DECKHAND_ANKI_BRIDGE_TOKEN": os.environ.get("DECKHAND_ANKI_BRIDGE_TOKEN", token),
        "DECKHAND_MCP_REQUIRE_TOKEN": "1" if settings.require_mcp_token() else "0",
    }
    _process = subprocess.Popen(  # noqa: S603 - launches bundled local companion
        command,
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        stdout=stdout,
        stderr=stderr,
        start_new_session=True,
    )
    _started_pid = _process.pid
    write_pid_file(_started_pid)
    if logger:
        logger("companion.started", pid=_started_pid, command=command)
    return _process


def stop_started_companion(logger=None, timeout: float = 2.0) -> dict[str, Any]:
    global _process, _started_pid
    pid = _started_pid
    if pid is None:
        return _status("not_owned", "no Anki-started companion recorded")
    return stop_companion_pid(pid, logger=logger, timeout=timeout, owned=True)


def stop_recorded_companion(logger=None, timeout: float = 2.0) -> dict[str, Any]:
    pid = _started_pid or read_pid_file()
    if pid is None:
        return _status("not_owned", "no Deckhand companion PID recorded")
    return stop_companion_pid(pid, logger=logger, timeout=timeout, owned=_started_pid is not None)


def stop_companion_pid(pid: int, logger=None, timeout: float = 2.0, *, owned: bool = False) -> dict[str, Any]:
    global _process, _started_pid
    if not process_alive(pid):
        clear_pid_file()
        _process = None
        _started_pid = None
        return _status("stopped", "recorded companion process is already gone", owned=owned, pid=pid)

    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        return _status("stop_failed", f"failed to stop companion: {exc}", owned=owned, pid=pid)

    deadline = time.time() + timeout
    while process_alive(pid) and time.time() < deadline:
        time.sleep(0.05)
    stopped = not process_alive(pid)
    if stopped:
        clear_pid_file()
        _process = None
        _started_pid = None
    result = _status("stopped" if stopped else "stopping", "companion stop requested", owned=owned, pid=pid)
    if logger:
        logger("companion.stop_requested", pid=pid, stopped=stopped)
    return result


def restart_companion(logger=None) -> dict[str, Any]:
    stop_recorded_companion(logger=logger)
    return ensure_running(logger=logger, force=True)


def start_companion_now(logger=None) -> dict[str, Any]:
    """Start the companion on user request, regardless of the autostart setting."""
    return ensure_running(logger=logger, force=True)


def runtime_status() -> dict[str, Any]:
    health = health_status()
    pid = _started_pid or read_pid_file()
    if health["healthy"] and not health.get("compatible", True):
        state = "stale"
        detail = "companion is reachable but does not match this add-on"
    elif health["healthy"]:
        state = "running"
        detail = "companion health check passed"
    else:
        state = "unavailable"
        detail = str(health.get("error") or "companion unavailable")
    return _status(
        state,
        detail,
        owned=_started_pid is not None,
        pid=pid,
        binary=bundled_server_path(),
        health=health,
    )


def health_status() -> dict[str, Any]:
    url = f"{companion_url()}/status"
    try:
        with urlopen(url, timeout=0.3) as response:  # noqa: S310 - loopback-only default URL
            payload = json.loads(response.read().decode("utf-8"))
        compatible = payload.get("service") == EXPECTED_SERVICE
        return {
            "healthy": bool(payload.get("ready")),
            "compatible": compatible,
            "staleReasons": [] if compatible else ["unexpected_service"],
            "url": url,
            "service": payload.get("service"),
            "version": payload.get("version"),
            "status": payload,
        }
    except (OSError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {"healthy": False, "compatible": False, "url": url, "error": str(exc)}


def process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def pid_file() -> Path:
    configured = os.environ.get("DECKHAND_COMPANION_PID_FILE")
    if configured:
        return Path(configured).expanduser()
    return default_runtime_dir() / "companion.pid"


def write_pid_file(pid: int) -> None:
    path = pid_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(pid), encoding="utf-8")


def read_pid_file() -> int | None:
    try:
        raw = pid_file().read_text(encoding="utf-8").strip()
        return int(raw) if raw else None
    except (OSError, ValueError):
        return None


def clear_pid_file() -> None:
    try:
        pid_file().unlink()
    except OSError:
        pass


def default_runtime_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "Deckhand" / "runtime"


def default_log_dir() -> Path:
    return Path.home() / "Library" / "Application Support" / "Deckhand" / "logs"


def _status(
    state: str,
    detail: str,
    *,
    owned: bool = False,
    pid: int | None = None,
    binary: Path | None = None,
    health: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "state": state,
        "detail": detail,
        "ownedByAnki": owned,
        "pid": pid,
        "binary": str(binary or bundled_server_path()),
        "url": companion_url(),
        "bridgeUrl": os.environ.get("DECKHAND_SAFE_BRIDGE_URL", f"ws://{companion_host()}:{companion_port()}/ws/anki"),
        "auth": {
            "scheme": "bearer",
            "tokenPresent": bool(companion_token()),
            "mcpTokenRequired": settings.require_mcp_token(),
        },
        "health": health or health_status(),
    }
