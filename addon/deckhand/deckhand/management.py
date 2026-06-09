from __future__ import annotations

import os
import platform
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

from . import companion
from .bridge import bridge_status

DEFAULT_CDP_PORT = 9222
DEFAULT_COMPANION_URL = "http://127.0.0.1:18765"
BANNER_TITLE = "Let Deckhand control Anki"
BANNER_SUMMARY = "Restart once to let Deckhand inspect and operate Anki more reliably."
BANNER_BODY = (
    "Deckhand can inspect and operate Anki's deck, reviewer, browser, and editor more reliably "
    "when Anki is started with a local debugging port. This restart only changes the current "
    "Anki launch and keeps the port on your computer."
)
BANNER_PRIMARY_ACTION = "Restart Anki for Deckhand"
BANNER_DISMISS_ACTION = "Not now"
CONNECTED_TITLE = "Deckhand is connected to Anki"
CONNECTED_SUMMARY = "Extension running. Deckhand can inspect and operate Anki."
CONNECTED_DETAILS = (
    "The local debugging port is available, so Deckhand can coordinate with Anki's deck, "
    "reviewer, browser, and editor views."
)
CONNECTED_DISMISS_ACTION = "Dismiss"

_banner_dismissed = False
_banner_dock = None
_management_dialog = None


def cdp_port() -> int:
    raw = os.environ.get("DECKHAND_ANKI_CDP_PORT", str(DEFAULT_CDP_PORT))
    try:
        port = int(raw)
    except ValueError:
        return DEFAULT_CDP_PORT
    return port if 1 <= port <= 65535 else DEFAULT_CDP_PORT


def cdp_host() -> str:
    return os.environ.get("DECKHAND_ANKI_CDP_HOST", "127.0.0.1")


def cdp_status(host: str | None = None, port: int | None = None) -> dict[str, Any]:
    host = host or cdp_host()
    port = port or cdp_port()
    open_ = _port_open(host, port)
    return {
        "host": host,
        "port": port,
        "open": open_,
        "url": f"http://{host}:{port}/json/version",
        "launchEnv": f"QTWEBENGINE_REMOTE_DEBUGGING={port}",
    }


def companion_status() -> dict[str, Any]:
    runtime = companion.runtime_status()
    return {
        "runtime": runtime,
        "ankiBridge": bridge_status.to_dict(),
        "httpUrl": runtime["url"],
        "bridgeUrl": runtime["bridgeUrl"],
        "mcpUrl": f"{runtime['url'].rstrip('/')}/mcp",
    }


def management_snapshot(anki_tools: list[str] | None = None) -> dict[str, Any]:
    return {
        "cdp": cdp_status(),
        "companion": companion_status(),
        "ankiTools": anki_tools or [],
    }


def maybe_show_cdp_banner(mw: Any, logger=None) -> None:
    global _banner_dock
    if _banner_dismissed or os.environ.get("DECKHAND_CDP_BANNER_DISABLED") == "1":
        return
    status = cdp_status()
    if _banner_dock is not None:
        return
    try:
        from aqt.qt import Qt, QToolBar
    except Exception as exc:  # pragma: no cover - only meaningful inside Anki/Qt
        if logger:
            logger("management.cdp_banner_unavailable", error=str(exc))
        return
    toolbar = QToolBar("Deckhand Setup", mw)
    toolbar.setObjectName("deckhand_cdp_banner")
    toolbar.setMovable(False)
    toolbar.setFloatable(False)
    toolbar.addWidget(_make_cdp_banner_widget(toolbar, status, logger))
    mw.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)
    _banner_dock = toolbar
    if logger:
        logger("management.cdp_banner_shown", port=status["port"], enabled=bool(status["open"]))


def _make_cdp_banner_widget(dock: Any, status: dict[str, Any], logger=None) -> Any:
    from aqt.qt import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

    widget = QWidget(dock)
    outer = QVBoxLayout(widget)
    outer.setContentsMargins(6, 2, 6, 2)
    outer.setSpacing(2)

    row = QHBoxLayout()
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(8)
    enabled = bool(status.get("open"))
    title = QLabel(CONNECTED_TITLE if enabled else BANNER_TITLE)
    title.setStyleSheet("font-weight: 600;")
    row.addWidget(title)

    summary = QLabel(CONNECTED_SUMMARY if enabled else BANNER_SUMMARY)
    summary.setWordWrap(False)
    row.addWidget(summary, 1)

    learn_more = QPushButton("Learn more")
    learn_more.setFlat(True)
    row.addWidget(learn_more)

    if not enabled:
        restart = QPushButton(BANNER_PRIMARY_ACTION)
        restart.clicked.connect(lambda _checked=False: restart_anki_with_cdp(status["port"], logger=logger))
        restart.setDefault(True)
        row.addWidget(restart)

    dismiss = QPushButton(CONNECTED_DISMISS_ACTION if enabled else BANNER_DISMISS_ACTION)
    dismiss.clicked.connect(lambda _checked=False: dismiss_cdp_banner(dock, logger=logger))
    row.addWidget(dismiss)
    outer.addLayout(row)

    details = QFrame(widget)
    details_layout = QVBoxLayout(details)
    details_layout.setContentsMargins(0, 2, 0, 0)
    details_text = CONNECTED_DETAILS if enabled else BANNER_BODY
    body = QLabel(f"{details_text} Port: {status['port']}.")
    body.setWordWrap(True)
    details_layout.addWidget(body)
    details.setVisible(False)

    def toggle_details(_checked=False) -> None:
        expanded = not details.isVisible()
        details.setVisible(expanded)
        learn_more.setText("Show less" if expanded else "Learn more")
        if logger:
            logger("management.cdp_banner_details_toggled", expanded=expanded)

    learn_more.clicked.connect(toggle_details)
    outer.addWidget(details)
    return widget


def dismiss_cdp_banner(dock: Any | None = None, logger=None) -> None:
    global _banner_dismissed, _banner_dock
    _banner_dismissed = True
    target = dock or _banner_dock
    if target is not None:
        target.hide()
        if hasattr(target, "deleteLater"):
            target.deleteLater()
    _banner_dock = None
    if logger:
        logger("management.cdp_banner_dismissed")


def show_management_dialog(mw: Any, anki_tools: list[str], logger=None) -> None:
    global _management_dialog
    if _management_dialog is not None:
        try:
            _management_dialog.show()
            _management_dialog.raise_()
            _management_dialog.activateWindow()
            return
        except Exception:
            _management_dialog = None
    try:
        dialog = _build_management_dialog(mw, anki_tools, logger=logger)
    except Exception as exc:  # noqa: BLE001 - surface Qt failures in a plain dialog
        if logger:
            logger("management.dialog_unavailable", error=str(exc))
        raise
    dialog.finished.connect(lambda _result: _clear_management_dialog())
    _management_dialog = dialog
    dialog.show()
    if logger:
        logger("management.dialog_opened")


def _clear_management_dialog() -> None:
    global _management_dialog
    _management_dialog = None


def _build_management_dialog(mw: Any, anki_tools: list[str], logger=None) -> Any:
    from aqt.qt import (
        QDialog,
        QDialogButtonBox,
        QGuiApplication,
        QGridLayout,
        QGroupBox,
        QLabel,
        QLineEdit,
        QPushButton,
        QTextEdit,
        QVBoxLayout,
    )

    dialog = QDialog(mw)
    dialog.setWindowTitle("Deckhand Setup")
    dialog.resize(620, 420)
    layout = QVBoxLayout(dialog)

    cdp = cdp_status()
    browser_group = QGroupBox("Browser Control")
    browser_layout = QGridLayout(browser_group)
    browser_layout.addWidget(QLabel("Status"), 0, 0)
    browser_layout.addWidget(QLabel("available" if cdp["open"] else "restart needed"), 0, 1)
    browser_layout.addWidget(QLabel("What it enables"), 1, 0)
    browser_detail = QLabel("Deckhand can inspect and operate Anki views more reliably.")
    browser_detail.setWordWrap(True)
    browser_layout.addWidget(browser_detail, 1, 1)
    restart_anki_button = QPushButton(BANNER_PRIMARY_ACTION)
    restart_anki_button.setEnabled(not bool(cdp["open"]))
    restart_anki_button.clicked.connect(lambda _checked=False: restart_anki_with_cdp(cdp["port"], logger=logger))
    browser_layout.addWidget(restart_anki_button, 2, 0, 1, 2)
    layout.addWidget(browser_group)

    companion_group = QGroupBox("Companion Server")
    companion_layout = QGridLayout(companion_group)
    companion = companion_status()
    runtime = companion["runtime"]
    companion_layout.addWidget(QLabel("Helper"), 0, 0)
    companion_layout.addWidget(QLabel(_user_status(runtime.get("state", "unknown"))), 0, 1)
    companion_layout.addWidget(QLabel("Connected to Anki"), 1, 0)
    companion_layout.addWidget(QLabel("yes" if runtime.get("ownedByAnki") else "not yet"), 1, 1)
    companion_layout.addWidget(QLabel("Bridge"), 2, 0)
    companion_layout.addWidget(QLabel(_user_status(companion["ankiBridge"].get("state", "unknown"))), 2, 1)
    detail = QLabel(str(companion["ankiBridge"].get("detail", "")))
    detail.setWordWrap(True)
    companion_layout.addWidget(QLabel("Detail"), 3, 0)
    companion_layout.addWidget(detail, 3, 1)
    restart_companion_button = QPushButton("Restart Deckhand helper")
    restart_companion_button.setEnabled(bool(runtime.get("pid")))

    def restart_companion() -> None:
        result = companion_module_restart(logger=logger)
        if logger:
            logger("management.companion_restart_requested", result=result)

    restart_companion_button.clicked.connect(lambda _checked=False: restart_companion())
    companion_layout.addWidget(restart_companion_button, 4, 0, 1, 2)
    layout.addWidget(companion_group)

    install_group = QGroupBox("Connect an MCP client")
    install_layout = QVBoxLayout(install_group)
    mcp_url = str(companion["mcpUrl"])
    url_row = QGridLayout()
    url_row.addWidget(QLabel("MCP server URL"), 0, 0)
    url_field = QLineEdit(mcp_url)
    url_field.setReadOnly(True)
    url_row.addWidget(url_field, 0, 1)
    copy_button = QPushButton("Copy MCP URL")

    def copy_mcp_url() -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(mcp_url)
        if logger:
            logger("management.mcp_url_copied")

    copy_button.clicked.connect(lambda _checked=False: copy_mcp_url())
    url_row.addWidget(copy_button, 0, 2)
    install_layout.addLayout(url_row)

    instructions = QTextEdit()
    instructions.setReadOnly(True)
    instructions.setPlainText(mcp_install_instructions(mcp_url))
    install_layout.addWidget(instructions)
    layout.addWidget(install_group, 1)

    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.rejected.connect(dialog.reject)
    buttons.accepted.connect(dialog.accept)
    layout.addWidget(buttons)
    return dialog


def mcp_install_instructions(mcp_url: str) -> str:
    return (
        "Deckhand exposes one standard Streamable HTTP MCP endpoint.\n\n"
        "1. Open your MCP client's server settings.\n"
        "2. Add a Streamable HTTP MCP server.\n"
        f"3. Paste this server URL: {mcp_url}\n"
        "4. Save, then reconnect or refresh the client's tools list."
    )


def _user_status(value: Any) -> str:
    return str(value or "unknown").replace("_", " ")


def companion_module_restart(logger=None) -> dict[str, Any]:
    return companion.restart_companion(logger=logger)


def restart_anki_with_cdp(port: int | None = None, logger=None) -> dict[str, Any]:
    port = port or cdp_port()
    command = restart_command(port)
    if logger:
        logger("management.cdp_restart_requested", command=command, port=port)
    subprocess.Popen(command, start_new_session=True)  # noqa: S603 - explicit local app restart command
    return {"ok": True, "port": port, "command": command}


def restart_command(port: int | None = None) -> list[str]:
    port = port or cdp_port()
    if platform.system() == "Darwin":
        executable = anki_executable_path()
        log_path = Path.home() / "Library" / "Logs" / "Deckhand" / "anki-cdp-restart.log"
        script = (
            "osascript -e 'tell application \"Anki\" to quit' >/dev/null 2>&1 || true\n"
            "sleep 2\n"
            "if pgrep -f 'aqt.run\\(\\)' >/dev/null; then\n"
            "  pkill -f 'aqt.run\\(\\)' || true\n"
            "  sleep 1\n"
            "fi\n"
            f"mkdir -p {sh_quote(str(log_path.parent))}\n"
            f"QTWEBENGINE_REMOTE_DEBUGGING={port} nohup {sh_quote(str(executable))} "
            f">>{sh_quote(str(log_path))} 2>&1 &\n"
        )
        return ["/bin/sh", "-lc", script]
    return [
        "sh",
        "-lc",
        f"QTWEBENGINE_REMOTE_DEBUGGING={port} anki >/tmp/deckhand-anki-cdp.log 2>&1 &",
    ]


def anki_executable_path() -> Path:
    configured = os.environ.get("DECKHAND_ANKI_EXECUTABLE")
    if configured:
        return Path(configured).expanduser()
    app_path = Path(os.environ.get("DECKHAND_ANKI_APP_PATH", "/Applications/Anki.app")).expanduser()
    for name in ("launcher", "Anki"):
        candidate = app_path / "Contents" / "MacOS" / name
        if candidate.exists():
            return candidate
    return app_path / "Contents" / "MacOS" / "launcher"


def sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False
