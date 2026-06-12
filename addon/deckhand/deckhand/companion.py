from __future__ import annotations

import json
import os
import platform
import secrets
import shutil
import signal
import subprocess
import time
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from . import settings
from .state_paths import runtime_root, work_root
from .version import ADDON_VERSION


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = settings.DEFAULT_COMPANION_PORT
EXPECTED_SERVICE = "deckhand-anki-companion"
IS_WINDOWS = platform.system().lower() == "windows"
SERVER_BINARY = "deckhand-server.exe" if IS_WINDOWS else "deckhand-server"

_process: subprocess.Popen | None = None
_started_pid: int | None = None
_companion_token: str | None = None
_log_handles: list[Any] = []


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


def companion_server_path() -> Path:
    configured = os.environ.get("DECKHAND_COMPANION_BINARY")
    if configured:
        return Path(configured).expanduser()
    return runtime_server_path()


def runtime_server_path() -> Path:
    return default_runtime_dir() / "bin" / ADDON_VERSION / platform_tag() / SERVER_BINARY


def prepare_runtime_server_binary(source: Path | None = None) -> Path:
    if os.environ.get("DECKHAND_COMPANION_BINARY"):
        return bundled_server_path()
    source = source or bundled_server_path()
    destination = runtime_server_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if _should_copy_binary(source, destination):
        shutil.copy2(source, destination)
    ensure_executable(destination)
    prune_stale_runtime_binaries()
    return destination


def prune_stale_runtime_binaries() -> None:
    """Drop runtime copies left behind by previous add-on versions.

    Best-effort: a still-running old helper keeps its binary locked on
    Windows, so failures are ignored and retried on the next start.
    """
    bin_root = default_runtime_dir() / "bin"
    try:
        stale_dirs = [path for path in bin_root.iterdir() if path.name != ADDON_VERSION]
    except OSError:
        return
    for path in stale_dirs:
        shutil.rmtree(path, ignore_errors=True)


def _should_copy_binary(source: Path, destination: Path) -> bool:
    try:
        source_stat = source.stat()
        destination_stat = destination.stat()
    except OSError:
        return True
    return source_stat.st_size != destination_stat.st_size or int(source_stat.st_mtime) > int(destination_stat.st_mtime)


def platform_tag(system: str | None = None, machine: str | None = None) -> str:
    system = (system or platform.system()).lower()
    machine = (machine or platform.machine()).lower()
    machine = {"amd64": "x86_64", "x64": "x86_64", "arm64": "aarch64"}.get(machine, machine)
    system = {"darwin": "macos"}.get(system, system)
    return f"{system}-{machine}"


def ensure_running(logger=None, *, force: bool = False) -> dict[str, Any]:
    if os.environ.get("DECKHAND_COMPANION_DISABLED") == "1":
        return _status("disabled", "companion autostart disabled")
    stopped_upgrade = stop_stale_recorded_companion(logger=logger)
    if stopped_upgrade.get("state") == "stopping":
        # The old helper still holds the port; a new process would fail to
        # bind and clobber the recorded PID of the one that is shutting down.
        return _status(
            "stopping",
            "previous companion is still stopping; not starting a replacement yet",
            pid=stopped_upgrade.get("pid"),
        )
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
        detail = "companion already healthy"
        if stopped_upgrade.get("state") == "stopped":
            detail = "companion restarted after add-on upgrade"
        return _status(
            "running",
            detail,
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

    bundled_binary = bundled_server_path()
    if not bundled_binary.exists():
        return _status("missing", f"companion binary not found: {bundled_binary}", binary=bundled_binary)
    try:
        binary = prepare_runtime_server_binary(bundled_binary)
    except OSError as exc:
        return _status("missing", f"failed to prepare companion binary: {exc}", binary=bundled_binary)

    process = start_companion(binary, logger=logger)
    deadline = time.time() + float(os.environ.get("DECKHAND_COMPANION_START_TIMEOUT_SECONDS", "3"))
    health = health_status()
    while not health["healthy"] and time.time() < deadline:
        time.sleep(0.1)
        health = health_status()
    state = "running" if health["healthy"] else "starting"
    detail = "companion started by Anki" if health["healthy"] else "companion process started; health check pending"
    return _status(state, detail, owned=True, pid=process.pid, binary=binary, health=health)


def ensure_executable(binary: Path) -> None:
    """Restore the exec bit: .ankiaddon zips are extracted without permissions."""
    try:
        if not os.access(binary, os.X_OK):
            os.chmod(binary, 0o755)
    except OSError:
        pass


def _close_log_handles() -> None:
    for handle in _log_handles:
        try:
            handle.close()
        except OSError:
            pass
    _log_handles.clear()


def start_companion(binary: Path, logger=None) -> subprocess.Popen:
    global _process, _started_pid
    ensure_executable(binary)
    log_dir = Path(os.environ.get("DECKHAND_COMPANION_LOG_DIR", str(default_log_dir()))).expanduser()
    log_dir.mkdir(parents=True, exist_ok=True)
    _close_log_handles()
    stdout = (log_dir / "companion.stdout.log").open("ab")
    stderr = (log_dir / "companion.stderr.log").open("ab")
    _log_handles.extend([stdout, stderr])
    command = [str(binary), "serve", "--bind", companion_bind(), "--parent-pid", str(os.getpid())]
    token = companion_token()
    env = {
        **os.environ,
        "DECKHAND_COMPANION_LOG": str(log_dir / "companion.trace.log"),
        "DECKHAND_COMPANION_TOKEN": token,
        "DECKHAND_ANKI_BRIDGE_TOKEN": os.environ.get("DECKHAND_ANKI_BRIDGE_TOKEN", token),
        "DECKHAND_MCP_REQUIRE_TOKEN": "1" if settings.require_mcp_token() else "0",
        # Keep the Rust server's state root identical to the add-on's.
        "DECKHAND_ANKI_EXTENSION_STATE_ROOT": str(work_root()),
    }
    if IS_WINDOWS:
        # start_new_session is silently ignored on Windows; the console-subsystem
        # helper would otherwise flash a console window when launched from Anki.
        detach_kwargs: dict[str, Any] = {
            "creationflags": subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP,
        }
    else:
        detach_kwargs = {"start_new_session": True}
    _process = subprocess.Popen(  # noqa: S603 - launches bundled local companion
        command,
        cwd=str(default_runtime_dir()),
        env=env,
        stdout=stdout,
        stderr=stderr,
        **detach_kwargs,
    )
    _started_pid = _process.pid
    write_pid_file(_started_pid)
    write_owner_file(_started_pid, binary)
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


def stop_stale_recorded_companion(logger=None) -> dict[str, Any]:
    owner = read_owner_file()
    if not owner:
        return {"state": "not_owned", "detail": "no Deckhand companion owner metadata recorded"}
    if str(owner.get("addonVersion") or "") == ADDON_VERSION:
        return {"state": "current", "detail": "recorded companion matches this add-on", "pid": read_pid_file()}
    pid = read_pid_file()
    if pid is None:
        clear_owner_file()
        return {"state": "not_owned", "detail": "stale companion owner metadata had no PID"}
    if not process_alive(pid):
        clear_pid_file()
        clear_owner_file()
        return {"state": "stopped", "detail": "stale companion process is already gone", "pid": pid}
    if health_status().get("service") != EXPECTED_SERVICE:
        # The recorded PID is alive but nothing on the companion port answers
        # as Deckhand; the PID may have been reused by an unrelated process
        # (e.g. after a reboot), so never kill it on metadata alone.
        clear_owner_file()
        if logger:
            logger("companion.upgrade_stop_skipped", pid=pid, reason="unverified_process")
        return {
            "state": "skipped",
            "detail": "recorded process could not be verified as a Deckhand companion; left running",
            "pid": pid,
        }
    result = stop_companion_pid(pid, logger=logger, owned=True)
    if logger:
        logger(
            "companion.upgrade_stop_requested",
            previousAddonVersion=owner.get("addonVersion"),
            currentAddonVersion=ADDON_VERSION,
            pid=pid,
            stopped=result.get("state") == "stopped",
        )
    return result


def stop_companion_pid(pid: int, logger=None, timeout: float = 2.0, *, owned: bool = False) -> dict[str, Any]:
    global _process, _started_pid
    if not process_alive(pid):
        clear_pid_file()
        clear_owner_file()
        _process = None
        _started_pid = None
        return _status("stopped", "recorded companion process is already gone", owned=owned, pid=pid)

    try:
        terminate_process(pid)
    except OSError as exc:
        return _status("stop_failed", f"failed to stop companion: {exc}", owned=owned, pid=pid)

    deadline = time.time() + timeout
    while process_alive(pid) and time.time() < deadline:
        time.sleep(0.05)
    stopped = not process_alive(pid)
    if stopped:
        clear_pid_file()
        clear_owner_file()
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
        binary=companion_server_path(),
        health=health,
    )


def health_status() -> dict[str, Any]:
    url = f"{companion_url()}/status"
    try:
        with urlopen(url, timeout=0.3) as response:  # noqa: S310 - loopback-only default URL
            payload = json.loads(response.read().decode("utf-8"))
        service_ok = payload.get("service") == EXPECTED_SERVICE
        # The server crate version is kept in lockstep with ADDON_VERSION (a
        # unit test enforces it), so a mismatch means a stale helper from a
        # previous add-on version is still answering on the port.
        version_ok = str(payload.get("version") or "") == ADDON_VERSION
        stale_reasons = []
        if not service_ok:
            stale_reasons.append("unexpected_service")
        elif not version_ok:
            stale_reasons.append("version_mismatch")
        return {
            "healthy": bool(payload.get("ready")),
            "compatible": service_ok and version_ok,
            "staleReasons": stale_reasons,
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
    if IS_WINDOWS:
        # os.kill(pid, 0) is NOT a liveness probe on Windows: any signal other
        # than CTRL_C/CTRL_BREAK calls TerminateProcess and kills the target.
        return _windows_process_alive(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def terminate_process(pid: int) -> None:
    """Request process termination; raises OSError on failure."""
    if IS_WINDOWS:
        _windows_terminate_process(pid)
        return
    os.kill(pid, signal.SIGTERM)


def _windows_process_alive(pid: int) -> bool:
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    ERROR_ACCESS_DENIED = 5
    STILL_ACTIVE = 259

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        # A process we lack query access to still exists.
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        exit_code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _windows_terminate_process(pid: int) -> None:
    import ctypes

    PROCESS_TERMINATE = 0x0001

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
    if not handle:
        raise OSError(f"OpenProcess(PROCESS_TERMINATE) failed for pid {pid}: error {ctypes.get_last_error()}")
    try:
        if not kernel32.TerminateProcess(handle, 15):
            raise OSError(f"TerminateProcess failed for pid {pid}: error {ctypes.get_last_error()}")
    finally:
        kernel32.CloseHandle(handle)


def pid_file() -> Path:
    return _runtime_file_candidates("DECKHAND_COMPANION_PID_FILE", "companion.pid")[0]


def owner_file() -> Path:
    return _runtime_file_candidates("DECKHAND_COMPANION_OWNER_FILE", "companion-owner.json")[0]


def _runtime_file_candidates(env_var: str, name: str) -> list[Path]:
    """Preferred path first, then legacy locations so upgrades can still find
    and stop helpers recorded by older add-on versions: the state root
    (0.1.8-0.1.9; the Windows roaming profile) and the hardcoded pre-0.1.8
    directory (which was used on every platform back then)."""
    configured = os.environ.get(env_var)
    if configured:
        return [Path(configured).expanduser()]
    paths = [default_runtime_dir() / name]
    legacy_dirs = [
        work_root() / "runtime",
        Path.home() / "Library" / "Application Support" / "Deckhand" / "runtime",
    ]
    for legacy in legacy_dirs:
        candidate = legacy / name
        if candidate not in paths:
            paths.append(candidate)
    return paths


def write_pid_file(pid: int) -> None:
    path = pid_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(pid), encoding="utf-8")


def write_owner_file(pid: int, binary: Path) -> None:
    path = owner_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "pid": pid,
                "addonVersion": ADDON_VERSION,
                "binary": str(binary),
                "createdAtMs": int(time.time() * 1000),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def read_pid_file() -> int | None:
    for path in _runtime_file_candidates("DECKHAND_COMPANION_PID_FILE", "companion.pid"):
        try:
            raw = path.read_text(encoding="utf-8").strip()
            if raw:
                return int(raw)
        except (OSError, ValueError):
            continue
    return None


def read_owner_file() -> dict[str, Any]:
    for path in _runtime_file_candidates("DECKHAND_COMPANION_OWNER_FILE", "companion-owner.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            return payload
    return {}


def clear_pid_file() -> None:
    for path in _runtime_file_candidates("DECKHAND_COMPANION_PID_FILE", "companion.pid"):
        try:
            path.unlink()
        except OSError:
            pass


def clear_owner_file() -> None:
    for path in _runtime_file_candidates("DECKHAND_COMPANION_OWNER_FILE", "companion-owner.json"):
        try:
            path.unlink()
        except OSError:
            pass


def default_runtime_dir() -> Path:
    return runtime_root() / "runtime"


def default_log_dir() -> Path:
    return runtime_root() / "logs"


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
        "binary": str(binary or companion_server_path()),
        "url": companion_url(),
        "bridgeUrl": os.environ.get("DECKHAND_SAFE_BRIDGE_URL", f"ws://{companion_host()}:{companion_port()}/ws/anki"),
        "auth": {
            "scheme": "bearer",
            "tokenPresent": bool(companion_token()),
            "mcpTokenRequired": settings.require_mcp_token(),
        },
        "health": health or health_status(),
    }
