from __future__ import annotations

import json
import os
import platform
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from . import companion
from . import connect_hosts
from . import settings
from . import skills
from . import ui
from . import updates
from .bridge import bridge_status
from .command_catalog import CommandCatalogEntry, command_catalog
from .version import ADDON_VERSION

DEFAULT_CDP_PORT = 9222
DEFAULT_COMPANION_URL = "http://127.0.0.1:28765"
BANNER_TITLE = "Let Deckhand control Anki"
BANNER_SUMMARY = "Restart once to let Deckhand inspect and operate Anki more reliably."
BANNER_BODY = (
    "Deckhand can inspect and operate Anki's deck, reviewer, browser, and editor more reliably "
    "when Anki is started with a local debugging port. This restart only changes the current "
    "Anki launch and keeps the port on your computer."
)
BANNER_PRIMARY_ACTION = "Restart Anki for Deckhand"
BANNER_DISMISS_ACTION = "Not now"

CLIENT_CLAUDE_DESKTOP = connect_hosts.CLIENT_CLAUDE_DESKTOP
CLIENT_CLAUDE_CODE = connect_hosts.CLIENT_CLAUDE_CODE
CLIENT_CODEX = connect_hosts.CLIENT_CODEX_CLI
CLIENT_CODEX_CLI = connect_hosts.CLIENT_CODEX_CLI
CLIENT_CODEX_DESKTOP = connect_hosts.CLIENT_CODEX_DESKTOP
CLIENT_OTHER = connect_hosts.CLIENT_OTHER
CONNECT_CLIENTS = connect_hosts.connect_hosts()

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
    if status["open"]:
        # Healthy needs no banner; the management dialog reports the details.
        return
    if settings.cdp_banner_dismissed():
        settings.set_cdp_banner_dismissed(False)
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
    title = QLabel(BANNER_TITLE)
    title.setStyleSheet("font-weight: 600;")
    row.addWidget(title)

    summary = QLabel(BANNER_SUMMARY)
    summary.setWordWrap(False)
    row.addWidget(summary, 1)

    learn_more = QPushButton("Learn more")
    learn_more.setFlat(True)
    row.addWidget(learn_more)

    restart = QPushButton(BANNER_PRIMARY_ACTION)
    restart.clicked.connect(lambda _checked=False: _restart_anki_for_deckhand_from_ui(widget, status["port"], logger=logger))
    restart.setDefault(True)
    row.addWidget(restart)

    dismiss = QPushButton(BANNER_DISMISS_ACTION)
    dismiss.clicked.connect(lambda _checked=False: dismiss_cdp_banner(dock, logger=logger))
    row.addWidget(dismiss)
    outer.addLayout(row)

    details = QFrame(widget)
    details_layout = QVBoxLayout(details)
    details_layout.setContentsMargins(0, 2, 0, 0)
    body = QLabel(f"{BANNER_BODY} Port: {status['port']}.")
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
    settings.set_cdp_banner_dismissed(True)
    target = dock or _banner_dock
    if target is not None:
        target.hide()
        if hasattr(target, "deleteLater"):
            target.deleteLater()
    _banner_dock = None
    if logger:
        logger("management.cdp_banner_dismissed")


def show_management_dialog(mw: Any, anki_tools: list[str], logger=None, initial_client: str | None = None) -> None:
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
        dialog = _build_management_dialog(mw, anki_tools, logger=logger, initial_client=initial_client)
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


def _build_management_dialog(mw: Any, anki_tools: list[str], logger=None, initial_client: str | None = None) -> Any:
    from aqt.qt import (
        QDialog,
        QDialogButtonBox,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QTabWidget,
        QVBoxLayout,
    )

    dialog = QDialog(mw)
    dialog.setWindowTitle(f"Deckhand {ADDON_VERSION}")
    dialog.resize(680, 540)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(24, 20, 24, 18)
    layout.setSpacing(12)

    status = companion_status()
    runtime = status["runtime"]
    bridge = status["ankiBridge"]
    connected = runtime.get("state") == "running" and bridge.get("state") == "connected"

    heading = QLabel(f"Deckhand {ADDON_VERSION}")
    heading.setStyleSheet("font-size: 24px; font-weight: 700;")
    layout.addWidget(heading)

    status_row = QHBoxLayout()
    status_row.setSpacing(10)
    status_row.addWidget(_status_pill("Connected" if connected else "Needs attention", "ok" if connected else "warn"))
    summary = QLabel("Ready for MCP clients." if connected else "Run a connection test on the Status tab to see what needs fixing.")
    summary.setWordWrap(True)
    status_row.addWidget(summary, 1)
    layout.addLayout(status_row)

    tabs = QTabWidget(dialog)
    tabs.addTab(_build_connect_tab(tabs, logger=logger, initial_client=initial_client), "Connect")
    tabs.addTab(_build_status_tab(tabs, anki_tools, logger=logger), "Status")
    tabs.addTab(_build_server_tab(tabs, logger=logger), "Server")
    tabs.addTab(_build_skills_tab(tabs, logger=logger), "Skills")
    tabs.addTab(_build_about_tab(tabs, logger=logger), "About")
    layout.addWidget(tabs, 1)

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


def _build_connect_tab(parent: Any, logger=None, initial_client: str | None = None) -> Any:
    from aqt.qt import (
        Qt,
        QFrame,
        QGuiApplication,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QPushButton,
        QScrollArea,
        QVBoxLayout,
        QWidget,
    )

    widget = QWidget(parent)
    layout = QVBoxLayout(widget)
    layout.setSpacing(12)

    status = companion_status()
    mcp_url = str(status["mcpUrl"])
    connected = status["runtime"].get("state") == "running" and status["ankiBridge"].get("state") == "connected"
    token = settings.persistent_token() if settings.require_mcp_token() else None
    selected_client = {"id": connect_hosts.normalize_client_id(initial_client)}

    ready_card = _section_frame()
    ready_layout = QVBoxLayout(ready_card)
    ready_layout.setContentsMargins(14, 12, 14, 12)
    ready_layout.setSpacing(8)
    ready_header = QHBoxLayout()
    ready_header.addWidget(_status_pill("Ready" if connected else "Needs attention", "ok" if connected else "warn"))
    ready_text = QLabel(
        "Deckhand is running locally. Choose where you want Anki tools to appear, then follow the steps below."
        if connected
        else "Deckhand is not fully connected yet. Run a connection test on the Status tab, then come back here."
    )
    ready_text.setWordWrap(True)
    ready_header.addWidget(ready_text, 1)
    ready_layout.addLayout(ready_header)

    url_row = QHBoxLayout()
    url_row.setSpacing(8)
    url_row.addWidget(QLabel("MCP endpoint"))
    url_field = QLineEdit(mcp_url)
    url_field.setReadOnly(True)
    url_row.addWidget(url_field, 1)
    copy_url_button = QPushButton("Copy")

    def copy_mcp_url() -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(mcp_url)
        if logger:
            logger("management.mcp_url_copied")

    copy_url_button.clicked.connect(lambda _checked=False: copy_mcp_url())
    url_row.addWidget(copy_url_button)
    ready_layout.addLayout(url_row)
    layout.addWidget(ready_card)

    layout.addWidget(_section_title("Pick your app"))

    recipe_view = ui.build_recipe_view(widget, logger=logger, log_prefix="management")

    def render(client_id: str | None = None) -> None:
        if client_id is not None:
            selected_client["id"] = connect_hosts.normalize_client_id(client_id)
        recipe_view["render"](connect_hosts.connect_recipe(selected_client["id"], mcp_url, token))
        pillbar["set_selected"](selected_client["id"])

    def choose_host(client_id: str) -> None:
        render(client_id)
        if logger:
            logger("management.connect_host_selected", client=selected_client["id"])

    pillbar = ui.build_host_pillbar(widget, choose_host)
    layout.addWidget(pillbar["widget"])

    scroll = QScrollArea(widget)
    scroll.setWidgetResizable(True)
    scroll.setFrameShape(QFrame.Shape.NoFrame)
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    scroll.setWidget(recipe_view["widget"])
    layout.addWidget(scroll, 1)

    render(selected_client["id"])
    return widget


def _build_status_tab(parent: Any, anki_tools: list[str], logger=None) -> Any:
    from aqt.qt import (
        QHBoxLayout,
        QLabel,
        QPlainTextEdit,
        QPushButton,
        QTimer,
        QVBoxLayout,
        QWidget,
    )

    widget = QWidget(parent)
    layout = QVBoxLayout(widget)
    layout.setSpacing(10)

    capability_card = _section_frame()
    capability_layout = QVBoxLayout(capability_card)
    capability_layout.setContentsMargins(14, 12, 14, 12)
    capability_layout.setSpacing(8)
    capability_layout.addWidget(_section_title("Capabilities"))

    pills: dict[str, Any] = {}
    for name in ("MCP tools", "Local helper", "Anki bridge", "WebEngine control"):
        row = QHBoxLayout()
        row.setSpacing(8)
        row.addWidget(QLabel(name), 1)
        pill = _status_pill("checking", "warn")
        pills[name] = pill
        row.addWidget(pill)
        capability_layout.addLayout(row)
    layout.addWidget(capability_card)

    def refresh() -> None:
        status = companion_status()
        runtime = status["runtime"]
        bridge = status["ankiBridge"]
        cdp = cdp_status()
        _update_pill(pills["MCP tools"], f"{len(anki_tools)} available", "ok" if anki_tools else "warn")
        _update_pill(pills["Local helper"], _user_status(runtime.get("state", "unknown")), "ok" if runtime.get("state") == "running" else "warn")
        _update_pill(pills["Anki bridge"], _user_status(bridge.get("state", "unknown")), "ok" if bridge.get("state") == "connected" else "warn")
        _update_pill(pills["WebEngine control"], "available" if cdp["open"] else "off (optional)", "ok" if cdp["open"] else "warn")
        webengine_restart_button.setEnabled(not bool(cdp["open"]))
        webengine_restart_button.setText("WebEngine control is available" if cdp["open"] else BANNER_PRIMARY_ACTION)

    results = QPlainTextEdit()
    results.setReadOnly(True)
    results.setPlaceholderText("Click \"Test connection\" to check every link between your MCP client and Anki.")

    test_button = QPushButton("Test connection")

    def run_test() -> None:
        checks = run_connection_checks()
        results.setPlainText(format_connection_checks(checks))
        if logger:
            logger("management.connection_test_run", passed=all(check["ok"] for check in checks))

    test_button.clicked.connect(lambda _checked=False: run_test())
    webengine_restart_button = QPushButton(BANNER_PRIMARY_ACTION)
    webengine_restart_button.clicked.connect(
        lambda _checked=False: _restart_anki_for_deckhand_from_ui(widget, cdp_status()["port"], logger=logger)
    )
    button_row = QHBoxLayout()
    button_row.addWidget(test_button)
    button_row.addWidget(webengine_restart_button)
    button_row.addStretch(1)
    layout.addLayout(button_row)
    layout.addWidget(results, 1)

    timer = QTimer(widget)
    timer.setInterval(3000)
    timer.timeout.connect(refresh)
    timer.start()
    refresh()
    return widget


def _build_server_tab(parent: Any, logger=None) -> Any:
    from aqt.qt import (
        QCheckBox,
        QDesktopServices,
        QGuiApplication,
        QHBoxLayout,
        QLabel,
        QLineEdit,
        QPushButton,
        QUrl,
        QVBoxLayout,
        QWidget,
    )

    widget = QWidget(parent)
    layout = QVBoxLayout(widget)
    layout.setSpacing(10)

    state_label = QLabel("")
    state_label.setWordWrap(True)
    layout.addWidget(state_label)

    def refresh_state() -> None:
        runtime = companion.runtime_status()
        state_label.setText(f"Helper: {_user_status(runtime.get('state', 'unknown'))} — {runtime.get('detail', '')}")

    controls = QHBoxLayout()
    controls.setSpacing(8)
    start_button = QPushButton("Start helper")
    stop_button = QPushButton("Stop helper")
    restart_button = QPushButton("Restart helper")

    def start_helper() -> None:
        companion.start_companion_now(logger=logger)
        if logger:
            logger("management.companion_start_requested")
        refresh_state()

    def stop_helper() -> None:
        companion.stop_recorded_companion(logger=logger)
        if logger:
            logger("management.companion_stop_requested")
        refresh_state()

    def restart_helper() -> None:
        _restart_companion_from_ui(logger=logger)
        refresh_state()

    start_button.clicked.connect(lambda _checked=False: start_helper())
    stop_button.clicked.connect(lambda _checked=False: stop_helper())
    restart_button.clicked.connect(lambda _checked=False: restart_helper())
    for button in (start_button, stop_button, restart_button):
        controls.addWidget(button)
    controls.addStretch(1)
    layout.addLayout(controls)

    port_card = _section_frame()
    port_layout = QVBoxLayout(port_card)
    port_layout.setContentsMargins(14, 12, 14, 12)
    port_layout.setSpacing(8)
    port_layout.addWidget(_section_title("Port"))
    port_row = QHBoxLayout()
    port_row.setSpacing(8)
    port_field = QLineEdit(str(settings.companion_port()))
    port_row.addWidget(port_field)
    apply_port_button = QPushButton("Apply")
    port_row.addWidget(apply_port_button)
    port_row.addStretch(1)
    port_layout.addLayout(port_row)
    port_hint = QLabel("")
    port_hint.setWordWrap(True)
    port_layout.addWidget(port_hint)

    def apply_port() -> None:
        try:
            requested = int(port_field.text().strip())
        except ValueError:
            port_hint.setText("Enter a number between 1 and 65535.")
            return
        saved = settings.set_companion_port(requested)
        port_field.setText(str(saved))
        port_hint.setText("Saved. Restart the helper, then update the endpoint URL in your MCP client.")
        if logger:
            logger("management.companion_port_changed", port=saved)

    apply_port_button.clicked.connect(lambda _checked=False: apply_port())
    layout.addWidget(port_card)

    autostart_box = QCheckBox("Start the helper automatically with Anki")
    autostart_box.setChecked(settings.companion_autostart())
    autostart_box.toggled.connect(lambda checked: settings.set_companion_autostart(bool(checked)))
    layout.addWidget(autostart_box)

    token_card = _section_frame()
    token_layout = QVBoxLayout(token_card)
    token_layout.setContentsMargins(14, 12, 14, 12)
    token_layout.setSpacing(8)
    token_layout.addWidget(_section_title("Security"))
    require_token_box = QCheckBox("Require access token for MCP connections")
    require_token_box.setChecked(settings.require_mcp_token())
    token_layout.addWidget(require_token_box)
    token_row = QHBoxLayout()
    token_row.setSpacing(8)
    token_field = QLineEdit("")
    token_field.setReadOnly(True)
    token_row.addWidget(token_field, 1)
    copy_token_button = QPushButton("Copy token")
    regenerate_token_button = QPushButton("Regenerate")
    token_row.addWidget(copy_token_button)
    token_row.addWidget(regenerate_token_button)
    token_layout.addLayout(token_row)
    token_hint = QLabel("")
    token_hint.setWordWrap(True)
    token_layout.addWidget(token_hint)

    def refresh_token() -> None:
        required = settings.require_mcp_token()
        token_field.setText(settings.persistent_token() if required else "")
        for control in (token_field, copy_token_button, regenerate_token_button):
            control.setEnabled(required)

    def toggle_token(checked: bool) -> None:
        settings.set_require_mcp_token(bool(checked))
        refresh_token()
        token_hint.setText("Restart the helper, then re-add the connection in your MCP client with this token.")
        if logger:
            logger("management.mcp_token_required_changed", required=bool(checked))

    def copy_token() -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(token_field.text())
        if logger:
            logger("management.mcp_token_copied")

    def regenerate_token() -> None:
        settings.regenerate_persistent_token()
        refresh_token()
        token_hint.setText("New token saved. Restart the helper and update your MCP client.")
        if logger:
            logger("management.mcp_token_regenerated")

    require_token_box.toggled.connect(toggle_token)
    copy_token_button.clicked.connect(lambda _checked=False: copy_token())
    regenerate_token_button.clicked.connect(lambda _checked=False: regenerate_token())
    refresh_token()
    layout.addWidget(token_card)

    logs_button = QPushButton("Open logs folder")
    logs_button.clicked.connect(
        lambda _checked=False: QDesktopServices.openUrl(QUrl.fromLocalFile(str(companion.default_log_dir())))
    )
    logs_row = QHBoxLayout()
    logs_row.addWidget(logs_button)
    logs_row.addStretch(1)
    layout.addLayout(logs_row)
    layout.addStretch(1)
    refresh_state()
    return widget


def _build_skills_tab(parent: Any, logger=None) -> Any:
    from aqt.qt import (
        QDesktopServices,
        QHBoxLayout,
        QLabel,
        QListWidget,
        QListWidgetItem,
        QPlainTextEdit,
        QPushButton,
        QUrl,
        QVBoxLayout,
        QWidget,
    )

    widget = QWidget(parent)
    layout = QVBoxLayout(widget)
    layout.setSpacing(10)

    intro = QLabel(
        "Deckhand ships agent skills that teach your assistant proven Anki workflows. "
        "Install them for Claude Code or Codex below, or open the folder to copy them into another client."
    )
    intro.setWordWrap(True)
    layout.addWidget(intro)

    skill_list = QListWidget()
    for skill in skills.bundled_skills():
        label = skill["name"] if not skill["description"] else f"{skill['name']}\n{skill['description']}"
        skill_list.addItem(QListWidgetItem(label))
    layout.addWidget(skill_list, 1)

    targets = skills.install_targets()
    locations = "   •   ".join(f"{target['label']}: {target['root']}" for target in targets)
    target_label = QLabel(f"Install locations — {locations}")
    target_label.setWordWrap(True)
    layout.addWidget(target_label)

    results = QPlainTextEdit()
    results.setReadOnly(True)
    results.setMaximumHeight(110)
    layout.addWidget(results)

    def install_skills(target: dict[str, Any]) -> None:
        outcomes = skills.install_all(target["root"])
        results.setPlainText(f"Installed for {target['label']}:\n{format_skill_install_results(outcomes)}")
        if logger:
            logger(
                "management.skills_install_requested",
                client=target["id"],
                results={outcome["skill"]: outcome["status"] for outcome in outcomes},
            )

    def check_skill_updates() -> None:
        from . import skills_updates

        outcome = skills_updates.check_and_sync(force=True)
        results.setPlainText(format_skill_sync_result(outcome))
        if logger:
            logger("management.skills_update_check_run", result={k: v for k, v in outcome.items() if k != "outcomes"})

    def open_bundled_folder() -> None:
        roots = skills.bundled_skill_roots()
        if roots:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(roots[0])))

    install_claude_button = QPushButton("Install skills for Claude Code")
    install_claude_button.clicked.connect(lambda _checked=False: install_skills(targets[0]))
    install_codex_button = QPushButton("Install skills for Codex")
    install_codex_button.clicked.connect(lambda _checked=False: install_skills(targets[1]))
    update_button = QPushButton("Check for skill updates")
    update_button.clicked.connect(lambda _checked=False: check_skill_updates())
    open_button = QPushButton("Open bundled skills folder")
    open_button.clicked.connect(lambda _checked=False: open_bundled_folder())

    button_row = QHBoxLayout()
    button_row.setSpacing(8)
    button_row.addWidget(install_claude_button)
    button_row.addWidget(install_codex_button)
    button_row.addWidget(update_button)
    button_row.addWidget(open_button)
    button_row.addStretch(1)
    layout.addLayout(button_row)
    return widget


def _build_about_tab(parent: Any, logger=None) -> Any:
    from aqt.qt import (
        QDesktopServices,
        QFormLayout,
        QGuiApplication,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QUrl,
        QVBoxLayout,
        QWidget,
    )

    widget = QWidget(parent)
    layout = QVBoxLayout(widget)
    layout.setSpacing(10)

    info = about_info()
    form = QFormLayout()
    form.addRow("Add-on version", QLabel(str(info["addonVersion"])))
    form.addRow("Helper version", QLabel(str(info["companionVersion"] or "not running")))
    form.addRow("Anki version", QLabel(str(info["ankiVersion"] or "unknown")))
    form.addRow("Platform", QLabel(str(info["platform"])))
    form.addRow("Settings file", QLabel(str(info["settingsPath"])))
    form.addRow("Logs folder", QLabel(str(info["logDir"])))
    layout.addLayout(form)

    update_label = QLabel("")
    update_label.setWordWrap(True)
    open_release_button = QPushButton("Open download page")
    open_release_button.setVisible(False)
    release_url = {"value": updates.releases_page_url()}

    def check_updates() -> None:
        result = updates.check_for_update(force=True)
        if result.get("error"):
            update_label.setText(f"Could not check for updates: {result['error']}")
        elif result.get("updateAvailable"):
            update_label.setText(
                f"Deckhand {result['latestVersion']} is available (you have {info['addonVersion']})."
            )
            release_url["value"] = str(result.get("url") or updates.releases_page_url())
            open_release_button.setVisible(True)
        else:
            update_label.setText(f"You're up to date ({info['addonVersion']}).")
        if logger:
            logger("management.update_check_run", result=result)

    open_release_button.clicked.connect(
        lambda _checked=False: QDesktopServices.openUrl(QUrl(release_url["value"]))
    )

    check_button = QPushButton("Check for updates")
    check_button.clicked.connect(lambda _checked=False: check_updates())
    if updates.is_ankiweb_install():
        check_button.setVisible(False)
        update_label.setText(
            "Installed from AnkiWeb: updates arrive through Anki itself "
            "(Tools, then Add-ons, then Check for Updates)."
        )

    copy_diagnostics_button = QPushButton("Copy diagnostics")

    def copy_diagnostics() -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(format_diagnostics())
        update_label.setText("Diagnostics copied. Paste them into your bug report or support message.")
        if logger:
            logger("management.diagnostics_copied")

    copy_diagnostics_button.clicked.connect(lambda _checked=False: copy_diagnostics())

    button_row = QHBoxLayout()
    button_row.setSpacing(8)
    button_row.addWidget(check_button)
    button_row.addWidget(open_release_button)
    button_row.addWidget(copy_diagnostics_button)
    button_row.addStretch(1)
    layout.addLayout(button_row)
    layout.addWidget(update_label)

    project_link = QLabel(f'<a href="{updates.releases_page_url()}">{updates.releases_page_url()}</a>')
    project_link.setOpenExternalLinks(True)
    layout.addWidget(project_link)
    layout.addStretch(1)
    return widget


def about_info() -> dict[str, Any]:
    health = companion.health_status()
    try:
        from aqt import appVersion

        anki_version = str(appVersion)
    except Exception:
        anki_version = None
    return {
        "addonVersion": ADDON_VERSION,
        "companionVersion": health.get("version"),
        "companionHealthy": bool(health.get("healthy")),
        "ankiVersion": anki_version,
        "platform": f"{platform.system()} {platform.machine()}",
        "pythonVersion": platform.python_version(),
        "settingsPath": str(settings.settings_path()),
        "logDir": str(companion.default_log_dir()),
        "repo": updates.update_repo(),
    }


def format_diagnostics() -> str:
    payload = {
        "about": about_info(),
        "settings": {
            "companionPort": settings.companion_port(),
            "companionAutostart": settings.companion_autostart(),
            "requireMcpToken": settings.require_mcp_token(),
        },
        "checks": run_connection_checks(),
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def connect_recipe(client_id: str, mcp_url: str, token: str | None = None) -> dict[str, Any]:
    recipe = connect_hosts.connect_recipe(client_id, mcp_url, token)
    return {**recipe, "steps": connect_hosts.plain_step_text(recipe)}


def run_connection_checks() -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []

    health = companion.health_status()
    helper_ok = bool(health.get("healthy")) and bool(health.get("compatible", True))
    if not health.get("healthy"):
        helper_detail = str(health.get("error") or "helper did not answer")
        helper_action = "Start the helper from the Server tab."
    elif not health.get("compatible", True):
        helper_detail = "another program answered on Deckhand's port"
        helper_action = "Change the port on the Server tab, then restart the helper."
    else:
        helper_detail = f"answered at {health.get('url')}"
        helper_action = ""
    checks.append({"name": "Local helper", "ok": helper_ok, "detail": helper_detail, "action": helper_action, "optional": False})

    mcp_url = f"{companion.companion_url().rstrip('/')}/mcp"
    mcp_detail = mcp_url
    if settings.require_mcp_token():
        mcp_detail += " (access token required)"
    checks.append(
        {
            "name": "MCP endpoint",
            "ok": helper_ok,
            "detail": mcp_detail,
            "action": "" if helper_ok else "Start the helper, then paste this URL into your MCP client.",
            "optional": False,
        }
    )

    bridge = bridge_status.to_dict()
    bridge_ok = bridge.get("state") == "connected"
    checks.append(
        {
            "name": "Anki bridge",
            "ok": bridge_ok,
            "detail": str(bridge.get("detail") or _user_status(bridge.get("state", "unknown"))),
            "action": "" if bridge_ok else "Restart the helper from the Server tab, then run this test again.",
            "optional": False,
        }
    )

    cdp = cdp_status()
    checks.append(
        {
            "name": "WebEngine control",
            "ok": bool(cdp["open"]),
            "detail": "available" if cdp["open"] else "not enabled",
            "action": "" if cdp["open"] else f'Optional: click "{BANNER_PRIMARY_ACTION}" on the developer panel to enable UI control.',
            "optional": True,
        }
    )
    return checks


def format_connection_checks(checks: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for check in checks:
        if check["ok"]:
            mark = "PASS"
        else:
            mark = "SKIP" if check.get("optional") else "FAIL"
        lines.append(f"[{mark}] {check['name']} — {check['detail']}")
        if not check["ok"] and check.get("action"):
            lines.append(f"       Next: {check['action']}")
    required = [check for check in checks if not check.get("optional")]
    if all(check["ok"] for check in required):
        lines.append("")
        lines.append("Everything required is working. Connect from your MCP client whenever you're ready.")
    return "\n".join(lines)


_SKILL_STATUS_LABELS = {
    skills.STATUS_INSTALLED: "installed",
    skills.STATUS_UPDATED: "updated",
    skills.STATUS_UP_TO_DATE: "already up to date",
    skills.STATUS_SKIPPED_MODIFIED: "skipped (you edited this skill; remove it to reinstall)",
    skills.STATUS_SKIPPED_UNMANAGED: "skipped (a skill with this name already exists)",
}


def format_skill_install_results(outcomes: list[dict[str, Any]]) -> str:
    if not outcomes:
        return "No bundled skills found."
    return "\n".join(
        f"{outcome['skill']}: {_SKILL_STATUS_LABELS.get(outcome['status'], outcome['status'])}"
        for outcome in outcomes
    )


def format_skill_sync_result(result: dict[str, Any]) -> str:
    if result.get("reason") == "no_managed_installs":
        return "No installed skills to update yet. Install skills first, then check again."
    if result.get("error"):
        return f"Could not check for skill updates: {result['error']}"
    outcomes = result.get("outcomes") or []
    if not outcomes:
        return "No installed skills matched the deckhand-skills repository."
    header = f"Checked {len(outcomes)} installed skill(s): {result.get('updated', 0)} updated."
    return header + "\n" + format_skill_install_results(outcomes)


def _update_pill(pill: Any, text: str, state: str) -> None:
    pill.setText(text)
    _apply_pill_style(pill, state)


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
    from aqt.qt import QDialog, QDialogButtonBox, QFormLayout, QLabel, QPushButton, QTabWidget, QVBoxLayout, QWidget

    dialog = QDialog(mw)
    dialog.setWindowTitle("Deckhand Developer Panel")
    dialog.resize(820, 620)
    layout = QVBoxLayout(dialog)

    tabs = QTabWidget(dialog)
    tabs.addTab(_build_connection_tab(tabs, anki_tools), "Connection")
    tabs.addTab(_build_webengine_tab(tabs, logger=logger), "WebEngine")
    tabs.addTab(_build_logs_tab(tabs, anki_tools), "Logs")
    layout.addWidget(tabs, 1)

    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.rejected.connect(dialog.reject)
    buttons.accepted.connect(dialog.accept)
    layout.addWidget(buttons)
    return dialog


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
    restart.clicked.connect(lambda _checked=False: _restart_anki_for_deckhand_from_ui(widget, cdp["port"], logger=logger))
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
                "namespace": "_".join(name.split("_")[:2]),
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
        "idempotentHint": bool(entry and risk == "read" and name.endswith(("_status", "_list", "_list_pages", "_get_profile", "_registry"))),
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
    return ui.status_pill(text, state)


def _apply_pill_style(label: Any, state: str) -> None:
    ui.apply_pill_style(label, state)


def _section_frame() -> Any:
    return ui.section_frame()


def _section_title(text: str) -> Any:
    return ui.section_title(text)


def _restart_companion_from_ui(logger=None) -> None:
    result = companion_module_restart(logger=logger)
    if logger:
        logger("management.companion_restart_requested", result=result)


def companion_module_restart(logger=None) -> dict[str, Any]:
    return companion.restart_companion(logger=logger)


def _restart_anki_for_deckhand_from_ui(parent: Any, port: int | None = None, logger=None) -> dict[str, Any]:
    from aqt.qt import QMessageBox

    result = restart_anki_with_cdp(port, logger=logger)
    if not result.get("ok"):
        QMessageBox.warning(
            parent,
            "Restart Anki for Deckhand",
            str(result.get("detail") or "Deckhand could not schedule the Anki restart."),
        )
    return result


def restart_anki_with_cdp(port: int | None = None, logger=None) -> dict[str, Any]:
    port = port or cdp_port()
    system = platform.system().lower()
    try:
        if system == "windows":
            executable = windows_anki_executable_path()
            validation_error = _invalid_restart_executable_detail(executable)
            if validation_error:
                if logger:
                    logger("management.cdp_restart_invalid_executable", executable=str(executable), error=validation_error)
                return {"ok": False, "port": port, "detail": validation_error}
            command = restart_command(port, wait_for_pid=os.getpid(), executable=executable)
        else:
            command = restart_command(port)
    except OSError as exc:
        if logger:
            logger("management.cdp_restart_prepare_failed", error=str(exc), port=port)
        return {
            "ok": False,
            "port": port,
            "detail": f"Deckhand could not prepare the Anki restart: {exc}",
        }
    if logger:
        logger("management.cdp_restart_requested", command=command, port=port)
    try:
        _popen_detached(command)  # noqa: S603 - explicit local app restart command
    except OSError as exc:
        if logger:
            logger("management.cdp_restart_failed", error=str(exc), command=command, port=port)
        return {
            "ok": False,
            "port": port,
            "command": command,
            "detail": f"Deckhand could not schedule the Anki restart: {exc}",
        }
    if system == "windows":
        close_result = _request_anki_close_for_restart(logger=logger)
        if not close_result.get("ok"):
            return {
                "ok": False,
                "port": port,
                "command": command,
                "detail": str(close_result.get("detail") or "Deckhand could not ask Anki to close for restart."),
            }
    return {"ok": True, "port": port, "command": command}


def restart_command(port: int | None = None, wait_for_pid: int | None = None, executable: Path | None = None) -> list[str]:
    port = port or cdp_port()
    if platform.system().lower() == "windows":
        executable = executable or windows_anki_executable_path()
        wait_for_pid = wait_for_pid or os.getpid()
        return _write_windows_restart_command(port, wait_for_pid=wait_for_pid, executable=executable)
    if platform.system() == "Darwin":
        executable = anki_executable_path()
        log_path = Path.home() / "Library" / "Logs" / "Deckhand" / "anki-cdp-restart.log"
        # The relaunch must wait until the old Anki is fully gone: the launcher
        # hands off to a still-running instance ("Already running; reusing
        # existing instance") and the debug-port env never takes effect.
        script = (
            "osascript -e 'tell application \"Anki\" to quit' >/dev/null 2>&1 || true\n"
            "for _ in $(seq 1 30); do\n"
            "  pgrep -f 'aqt.run\\(\\)' >/dev/null || break\n"
            "  sleep 0.5\n"
            "done\n"
            "if pgrep -f 'aqt.run\\(\\)' >/dev/null; then\n"
            "  pkill -f 'aqt.run\\(\\)' || true\n"
            "  for _ in $(seq 1 20); do\n"
            "    pgrep -f 'aqt.run\\(\\)' >/dev/null || break\n"
            "    sleep 0.5\n"
            "  done\n"
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


def _popen_detached(command: list[str]) -> subprocess.Popen:
    if platform.system().lower() == "windows":
        flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
        if flags:
            return subprocess.Popen(command, creationflags=flags)  # noqa: S603 - explicit local app restart command
    return subprocess.Popen(command, start_new_session=True)  # noqa: S603 - explicit local app restart command


def _write_windows_restart_command(port: int, *, wait_for_pid: int, executable: Path) -> list[str]:
    log_dir = companion.default_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "anki-cdp-restart.log"
    script_path = log_dir / "anki-cdp-restart.cmd"
    _append_windows_restart_log(
        log_path,
        f"scheduled restart worker for pid {int(wait_for_pid)} on port {int(port)} using {executable}",
    )
    script = "\r\n".join(
        [
            "@echo off",
            "setlocal EnableExtensions",
            f'set "LOG_PATH={log_path}"',
            f'set "ANKI_EXE={executable}"',
            f'set "PARENT_PID={int(wait_for_pid)}"',
            f'set "PORT={int(port)}"',
            '>>"%LOG_PATH%" echo [%DATE% %TIME%] restarter started for pid %PARENT_PID% on port %PORT%',
            "set /a WAITED=0",
            ":wait_parent",
            'tasklist /FI "PID eq %PARENT_PID%" /FO CSV /NH | findstr /C:"%PARENT_PID%" >nul 2>nul',
            "if errorlevel 1 goto wait_anki",
            "if %WAITED% GEQ 180 (",
            '  >>"%LOG_PATH%" echo [%DATE% %TIME%] timed out waiting for pid %PARENT_PID%',
            "  exit /b 2",
            ")",
            "timeout /t 1 /nobreak >nul",
            "set /a WAITED+=1",
            "goto wait_parent",
            ":wait_anki",
            "set /a WAITED_ANKI=0",
            ":wait_anki_loop",
            'tasklist /FI "IMAGENAME eq anki.exe" /FO CSV /NH | findstr /I /C:"anki.exe" >nul 2>nul',
            "if errorlevel 1 goto launch_anki",
            "if %WAITED_ANKI% GEQ 60 (",
            '  >>"%LOG_PATH%" echo [%DATE% %TIME%] anki.exe still visible; attempting relaunch anyway',
            "  goto launch_anki",
            ")",
            "timeout /t 1 /nobreak >nul",
            "set /a WAITED_ANKI+=1",
            "goto wait_anki_loop",
            ":launch_anki",
            'set "QTWEBENGINE_REMOTE_DEBUGGING=%PORT%"',
            '>>"%LOG_PATH%" echo [%DATE% %TIME%] launching "%ANKI_EXE%"',
            'start "" "%ANKI_EXE%"',
            "set RC=%ERRORLEVEL%",
            '>>"%LOG_PATH%" echo [%DATE% %TIME%] start returned %RC%',
            "exit /b %RC%",
            "",
        ]
    )
    script_path.write_text(script, encoding="utf-8")
    return ["cmd.exe", "/d", "/c", str(script_path)]


def _append_windows_restart_log(log_path: Path, message: str) -> None:
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    with log_path.open("a", encoding="utf-8") as handle:
        handle.write(f"{timestamp} {message}\n")


def _request_anki_close_for_restart(logger=None) -> dict[str, Any]:
    try:
        from aqt import mw
        from aqt.qt import QTimer
    except Exception as exc:  # pragma: no cover - only meaningful inside Anki/Qt
        if logger:
            logger("management.cdp_restart_close_unavailable", error=str(exc))
        return {"ok": False, "detail": f"Deckhand could not ask Anki to close for restart: {exc}"}

    close = getattr(mw, "close", None)
    if not callable(close):
        if logger:
            logger("management.cdp_restart_close_unavailable", error="mw.close_missing")
        return {"ok": False, "detail": "Deckhand could not ask Anki to close for restart."}
    QTimer.singleShot(0, close)
    if logger:
        logger("management.cdp_restart_close_requested")
    return {"ok": True}


def _invalid_restart_executable_detail(executable: Path) -> str:
    if not executable.exists():
        return f"Deckhand could not find Anki at {executable}. Set DECKHAND_ANKI_EXECUTABLE to Anki's anki.exe path and try again."
    if not executable.is_file():
        return f"Deckhand found {executable}, but it is not an executable file."
    return ""


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


def windows_anki_executable_path() -> Path:
    configured = os.environ.get("DECKHAND_ANKI_EXECUTABLE")
    if configured:
        return Path(configured).expanduser()
    executable = Path(sys.executable)
    if executable.name.lower() == "anki.exe":
        return executable
    local_app_data = os.environ.get("LOCALAPPDATA")
    candidates = []
    if local_app_data:
        candidates.append(Path(local_app_data) / "Programs" / "Anki" / "anki.exe")
    candidates.extend(
        [
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Anki" / "anki.exe",
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Anki" / "anki.exe",
        ]
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0] if candidates else Path(r"C:\Program Files\Anki\anki.exe")


def sh_quote(value: str) -> str:
    return "'" + value.replace("'", "'\"'\"'") + "'"


def _port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.2):
            return True
    except OSError:
        return False
