from __future__ import annotations

import json
import os
import platform
import socket
import subprocess
import time
from pathlib import Path
from typing import Any

from . import companion
from .bridge import bridge_status
from .command_catalog import CommandCatalogEntry, command_catalog

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
_developer_panel = None


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
    tools = anki_tools or []
    return {
        "cdp": cdp_status(),
        "companion": companion_status(),
        "ankiTools": tools,
        "toolCount": len(tools),
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
        QFrame,
        QGuiApplication,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QPushButton,
        QVBoxLayout,
    )

    dialog = QDialog(mw)
    dialog.setWindowTitle("Deckhand")
    dialog.resize(620, 360)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(24, 20, 24, 18)
    layout.setSpacing(14)

    cdp = cdp_status()
    companion = companion_status()
    runtime = companion["runtime"]
    bridge = companion["ankiBridge"]
    connected = runtime.get("state") == "running" and runtime.get("ownedByAnki") and bridge.get("state") == "connected"

    heading = QLabel("Deckhand")
    heading.setStyleSheet("font-size: 24px; font-weight: 700;")
    layout.addWidget(heading)

    status_row = QHBoxLayout()
    status_row.setSpacing(10)
    status_row.addWidget(_status_pill("Connected" if connected else "Needs attention", "ok" if connected else "warn"))
    summary = QLabel("Ready for MCP clients." if connected else "Check the local helper and Anki bridge below.")
    summary.setWordWrap(True)
    status_row.addWidget(summary, 1)
    layout.addLayout(status_row)

    mcp_url = str(companion["mcpUrl"])
    mcp_card = _section_frame()
    mcp_layout = QVBoxLayout(mcp_card)
    mcp_layout.setContentsMargins(14, 12, 14, 12)
    mcp_layout.setSpacing(8)
    mcp_layout.addWidget(_section_title("MCP Connection"))
    url_row = QHBoxLayout()
    url_row.setSpacing(8)
    url_field = QLineEdit(mcp_url)
    url_field.setReadOnly(True)
    url_row.addWidget(url_field, 1)
    copy_button = QPushButton("Copy")

    def copy_mcp_url() -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(mcp_url)
        if logger:
            logger("management.mcp_url_copied")

    copy_button.clicked.connect(lambda _checked=False: copy_mcp_url())
    url_row.addWidget(copy_button)
    mcp_layout.addLayout(url_row)
    hint = QLabel("Add this Streamable HTTP endpoint in your MCP client.")
    hint.setStyleSheet("color: #666;")
    mcp_layout.addWidget(hint)
    layout.addWidget(mcp_card)

    capability_card = _section_frame()
    capability_layout = QVBoxLayout(capability_card)
    capability_layout.setContentsMargins(14, 12, 14, 12)
    capability_layout.setSpacing(8)
    capability_layout.addWidget(_section_title("Capabilities"))
    capability_layout.addLayout(_status_row("MCP tools", f"{len(anki_tools)} available", "ok" if anki_tools else "warn"))
    capability_layout.addLayout(_status_row("Local helper", _user_status(runtime.get("state", "unknown")), "ok" if runtime.get("state") == "running" else "warn"))
    capability_layout.addLayout(_status_row("Anki bridge", _user_status(bridge.get("state", "unknown")), "ok" if bridge.get("state") == "connected" else "warn"))
    capability_layout.addLayout(_status_row("WebEngine control", "available" if cdp["open"] else "restart needed", "ok" if cdp["open"] else "warn"))
    layout.addWidget(capability_card)

    action_row = QHBoxLayout()
    action_row.setSpacing(8)
    if not cdp["open"]:
        restart_anki_button = QPushButton(BANNER_PRIMARY_ACTION)
        restart_anki_button.clicked.connect(lambda _checked=False: restart_anki_with_cdp(cdp["port"], logger=logger))
        action_row.addWidget(restart_anki_button)
    if runtime.get("pid"):
        restart_companion_button = QPushButton("Restart helper")
        restart_companion_button.clicked.connect(lambda _checked=False: _restart_companion_from_ui(logger=logger))
        action_row.addWidget(restart_companion_button)
    action_row.addStretch(1)
    layout.addLayout(action_row)

    footer = QHBoxLayout()
    footer.addStretch(1)
    developer_button = QPushButton("Developer Panel...")
    developer_button.clicked.connect(lambda _checked=False: show_developer_panel(mw, anki_tools, logger=logger))
    footer.addWidget(developer_button)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.rejected.connect(dialog.reject)
    buttons.accepted.connect(dialog.accept)
    footer.addWidget(buttons)
    layout.addLayout(footer)
    return dialog


def mcp_install_instructions(mcp_url: str) -> str:
    return (
        "Deckhand exposes one standard Streamable HTTP MCP endpoint.\n\n"
        "1. Open your MCP client's server settings.\n"
        "2. Add a Streamable HTTP MCP server.\n"
        f"3. Paste this server URL: {mcp_url}\n"
        "4. Save, then reconnect or refresh the client's tools list."
    )


def show_developer_panel(mw: Any, anki_tools: list[str], logger=None) -> None:
    global _developer_panel
    if _developer_panel is not None:
        try:
            _developer_panel.show()
            _developer_panel.raise_()
            _developer_panel.activateWindow()
            return
        except Exception:
            _developer_panel = None
    try:
        dialog = _build_developer_panel(mw, anki_tools, logger=logger)
    except Exception as exc:  # noqa: BLE001 - surface Qt failures in a plain dialog
        if logger:
            logger("management.developer_panel_unavailable", error=str(exc))
        raise
    dialog.finished.connect(lambda _result: _clear_developer_panel())
    _developer_panel = dialog
    dialog.show()
    if logger:
        logger("management.developer_panel_opened")


def _clear_developer_panel() -> None:
    global _developer_panel
    _developer_panel = None


def _build_developer_panel(mw: Any, anki_tools: list[str], logger=None) -> Any:
    from aqt.qt import QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton, QTabWidget, QVBoxLayout, QWidget

    dialog = QDialog(mw)
    dialog.setWindowTitle("Deckhand Developer Panel")
    dialog.resize(820, 620)
    layout = QVBoxLayout(dialog)

    tabs = QTabWidget(dialog)
    tabs.addTab(_build_tools_tab(tabs, anki_tools), "Tools")
    tabs.addTab(_build_connection_tab(tabs, anki_tools), "Connection")
    tabs.addTab(_build_webengine_tab(tabs, logger=logger), "WebEngine")
    tabs.addTab(_build_logs_tab(tabs, anki_tools), "Logs")
    layout.addWidget(tabs, 1)

    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.rejected.connect(dialog.reject)
    buttons.accepted.connect(dialog.accept)
    layout.addWidget(buttons)
    return dialog


def _build_tools_tab(parent: Any, anki_tools: list[str]) -> Any:
    from aqt.qt import QHBoxLayout, QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QVBoxLayout, QWidget

    widget = QWidget(parent)
    layout = QVBoxLayout(widget)
    search = QLineEdit()
    search.setPlaceholderText("Search tools by name, namespace, or description")
    layout.addWidget(search)

    body = QHBoxLayout()
    tool_list = QListWidget()
    detail = QPlainTextEdit()
    detail.setReadOnly(True)
    body.addWidget(tool_list, 2)
    body.addWidget(detail, 3)
    layout.addLayout(body, 1)

    models = tool_view_models(anki_tools)

    def render_detail(model: dict[str, Any]) -> None:
        detail.setPlainText(_tool_detail_text(model))

    def refill() -> None:
        needle = search.text().lower().strip()
        tool_list.clear()
        for model in models:
            haystack = " ".join(
                [
                    str(model["name"]),
                    str(model["namespace"]),
                    str(model["description"]),
                ]
            ).lower()
            if needle and needle not in haystack:
                continue
            item = QListWidgetItem(f"{model['name']}\n{model['description']}")
            item.setData(256, model)
            tool_list.addItem(item)
        if tool_list.count():
            tool_list.setCurrentRow(0)
            render_detail(tool_list.currentItem().data(256))
        else:
            detail.setPlainText("No matching tools.")

    def selection_changed() -> None:
        item = tool_list.currentItem()
        if item is not None:
            render_detail(item.data(256))

    search.textChanged.connect(lambda _text: refill())
    tool_list.currentItemChanged.connect(lambda _current, _previous: selection_changed())
    refill()
    return widget


def _build_connection_tab(parent: Any, anki_tools: list[str]) -> Any:
    from aqt.qt import QFormLayout, QLabel, QWidget

    widget = QWidget(parent)
    layout = QFormLayout(widget)
    companion = companion_status()
    runtime = companion["runtime"]
    profile = _safe_profile()
    layout.addRow("MCP endpoint", QLabel(str(companion["mcpUrl"])))
    layout.addRow("Companion URL", QLabel(str(companion["httpUrl"])))
    layout.addRow("Bridge URL", QLabel(str(companion["bridgeUrl"])))
    layout.addRow("Helper state", QLabel(_user_status(runtime.get("state", "unknown"))))
    layout.addRow("Bridge state", QLabel(_user_status(companion["ankiBridge"].get("state", "unknown"))))
    layout.addRow("Bridge detail", QLabel(str(companion["ankiBridge"].get("detail", ""))))
    layout.addRow("Profile", QLabel(str(profile.get("name") or "unknown")))
    layout.addRow("Collection open", QLabel("yes" if profile.get("collectionOpen") else "no"))
    layout.addRow("Effective tools", QLabel(str(len(anki_tools))))
    return widget


def _build_webengine_tab(parent: Any, logger=None) -> Any:
    from aqt.qt import QFormLayout, QLabel, QPushButton, QWidget

    widget = QWidget(parent)
    layout = QFormLayout(widget)
    cdp = cdp_status()
    layout.addRow("Status", QLabel("available" if cdp["open"] else "restart needed"))
    layout.addRow("Host", QLabel(str(cdp["host"])))
    layout.addRow("Port", QLabel(str(cdp["port"])))
    layout.addRow("Version URL", QLabel(str(cdp["url"])))
    layout.addRow("Launch env", QLabel(str(cdp["launchEnv"])))
    restart = QPushButton(BANNER_PRIMARY_ACTION)
    restart.setEnabled(not bool(cdp["open"]))
    restart.clicked.connect(lambda _checked=False: restart_anki_with_cdp(cdp["port"], logger=logger))
    layout.addRow("Action", restart)
    return widget


def _build_logs_tab(parent: Any, anki_tools: list[str]) -> Any:
    from aqt.qt import QPlainTextEdit, QVBoxLayout, QWidget

    widget = QWidget(parent)
    layout = QVBoxLayout(widget)
    diagnostics = QPlainTextEdit()
    diagnostics.setReadOnly(True)
    snapshot = management_snapshot(anki_tools)
    diagnostics.setPlainText(json.dumps(snapshot, indent=2, sort_keys=True))
    layout.addWidget(diagnostics)
    return widget


def tool_view_models(anki_tools: list[str]) -> list[dict[str, Any]]:
    catalog_by_name: dict[str, CommandCatalogEntry] = {entry.name: entry for entry in command_catalog()}
    models: list[dict[str, Any]] = []
    for name in sorted(set(anki_tools)):
        entry = catalog_by_name.get(name)
        schema = entry.input_schema.to_dict() if entry else {}
        required = list(schema.get("required", [])) if isinstance(schema, dict) else []
        annotations = _tool_annotations(name, entry)
        models.append(
            {
                "name": name,
                "namespace": ".".join(name.split(".")[:2]),
                "description": entry.description if entry else "No catalog metadata",
                "requiredInputs": required,
                "inputSchema": schema,
                "annotations": annotations,
                "known": entry is not None,
            }
        )
    return models


def _tool_annotations(name: str, entry: CommandCatalogEntry | None) -> dict[str, bool]:
    risk = entry.risk if entry else "unknown"
    return {
        "readOnlyHint": risk in {"read", "ui"},
        "destructiveHint": risk in {"destructive", "dev_exec", "system_exec"},
        "idempotentHint": bool(entry and risk == "read" and name.endswith((".status", ".list", ".list_pages", ".get_profile", ".registry"))),
        "openWorldHint": bool(getattr(entry, "open_world", False)) if entry else False,
    }


def _tool_detail_text(model: dict[str, Any]) -> str:
    annotations = ", ".join(label for label, enabled in model["annotations"].items() if enabled) or "mutating/local"
    required = ", ".join(model["requiredInputs"]) or "none"
    return "\n".join(
        [
            str(model["name"]),
            "",
            str(model["description"]),
            "",
            f"Namespace: {model['namespace']}",
            f"Annotations: {annotations}",
            f"Required inputs: {required}",
            "",
            "Input schema:",
            json.dumps(model["inputSchema"], indent=2, sort_keys=True),
        ]
    )


def _safe_profile() -> dict[str, Any]:
    try:
        from aqt import mw
        from . import context_tools

        return context_tools.current_profile(mw)
    except Exception:
        return {"name": None, "collectionOpen": False}


def _user_status(value: Any) -> str:
    return str(value or "unknown").replace("_", " ")


def _status_pill(text: str, state: str) -> Any:
    from aqt.qt import QLabel

    label = QLabel(text)
    colors = {
        "ok": ("#0f5132", "#d1e7dd"),
        "warn": ("#664d03", "#fff3cd"),
    }
    fg, bg = colors.get(state, ("#343a40", "#e9ecef"))
    label.setStyleSheet(f"color: {fg}; background: {bg}; border-radius: 10px; padding: 3px 9px; font-weight: 600;")
    return label


def _section_frame() -> Any:
    from aqt.qt import QFrame

    frame = QFrame()
    frame.setObjectName("deckhand_section")
    frame.setStyleSheet("#deckhand_section { border: 1px solid #d9d9d9; border-radius: 8px; background: #fafafa; }")
    return frame


def _section_title(text: str) -> Any:
    from aqt.qt import QLabel

    label = QLabel(text)
    label.setStyleSheet("font-weight: 700;")
    return label


def _status_row(label: str, value: str, state: str) -> Any:
    from aqt.qt import QHBoxLayout, QLabel

    row = QHBoxLayout()
    row.setSpacing(8)
    row.addWidget(QLabel(label), 1)
    row.addWidget(_status_pill(value, state))
    return row


def _restart_companion_from_ui(logger=None) -> None:
    result = companion_module_restart(logger=logger)
    if logger:
        logger("management.companion_restart_requested", result=result)


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
