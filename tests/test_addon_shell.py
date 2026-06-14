import sys
import ast
import base64
import json
import os
import socket
import tempfile
import threading
import time
import unittest
import zipfile
from unittest import mock
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "addon" / "deckhand"
sys.path.insert(0, str(ADDON))
TEST_TMP = Path(tempfile.gettempdir()) / "deckhand-anki-tests"

from deckhand.bridge import BridgeStatus
from deckhand.capabilities import anki_bridge_capability_payload, capability_payload
from deckhand.command_catalog import command_catalog, validate_command_catalog
from deckhand import runtime_snippets
from deckhand import addon as addon_shell
from deckhand import bridge_transport
from deckhand import import_export_tools
from deckhand import connect_hosts
from deckhand import management
from deckhand import typed_tools
from deckhand import context_tools
from deckhand import card_tools
from deckhand import companion
from deckhand import media_tools
from deckhand import runtime_tools
from deckhand import structure_tools
from deckhand import settings
from deckhand import skills
from deckhand import skills_updates
from deckhand import state_paths
from deckhand import tts
from deckhand import tool_visibility
from deckhand import updates
from deckhand import welcome
from deckhand import web
from deckhand import webengine_tools
from deckhand.direct_executor import DirectExecutor
from deckhand.version import ADDON_VERSION


def _qt_import_names(nodes) -> set[str]:
    names: set[str] = set()
    for node in nodes:
        if isinstance(node, ast.ImportFrom) and node.module == "aqt.qt":
            names.update(alias.asname or alias.name for alias in node.names)
    return names


def _direct_qt_name_uses(function: ast.FunctionDef | ast.AsyncFunctionDef, qt_names: set[str]) -> set[str]:
    used: set[str] = set()
    for child in ast.iter_child_nodes(function):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        for node in ast.walk(child):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(node, ast.Name) and node.id in qt_names:
                used.add(node.id)
    return used


def _direct_qt_import_names(function: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    imported: set[str] = set()
    for child in ast.iter_child_nodes(function):
        if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            continue
        for node in ast.walk(child):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if isinstance(node, ast.ImportFrom) and node.module == "aqt.qt":
                imported.update(alias.asname or alias.name for alias in node.names)
    return imported


class AddonShellTests(unittest.TestCase):
    def test_capability_payload_marks_internal_bridge_path(self):
        payload = capability_payload()
        self.assertEqual(payload["paths"], ["safe_bridge"])
        self.assertEqual(
            {tool["name"] for tool in payload["tools"]},
            {"anki_backup_create", "anki_run_python", "anki_runtime_info"},
        )
        self.assertIn("catalog", payload)
        self.assertTrue(all(tool["path"] == "safe_bridge" for tool in payload["tools"]))

    def test_anki_bridge_capability_payload_advertises_internal_anki_tools(self):
        payload = anki_bridge_capability_payload()
        names = {tool["name"] for tool in payload["tools"]}

        self.assertEqual(payload["paths"], ["safe_bridge"])
        self.assertEqual(names, {"anki_backup_create", "anki_run_python", "anki_runtime_info"})

    def test_tool_visibility_is_fixed_to_public_runtime_surface(self):
        visible = tool_visibility.visible_tool_names(
            ["anki_backup_create", "anki_deck_list", "anki_run_python", "anki_runtime_info"]
        )

        self.assertEqual(visible, ["anki_backup_create", "anki_run_python", "anki_runtime_info"])

    def test_tool_visibility_templates_do_not_expand_public_surface(self):
        visible = tool_visibility.template_tool_names(
            "any-template",
            ["anki_backup_create", "anki_deck_list", "anki_run_python", "anki_runtime_info"],
        )

        self.assertEqual(visible, ["anki_backup_create", "anki_run_python", "anki_runtime_info"])

    def test_runtime_info_reports_compact_environment_context(self):
        collection = SimpleNamespace(
            sched=SimpleNamespace(version=3),
            media=SimpleNamespace(dir=lambda: "/tmp/collection.media"),
        )
        mw = SimpleNamespace(
            state="deckBrowser",
            col=collection,
            pm=SimpleNamespace(name=lambda: "Test User", base="/tmp/Anki2"),
            addonManager=SimpleNamespace(addonsFolder=lambda: "/tmp/addons21"),
        )

        info = runtime_tools.runtime_info(mw)

        self.assertIn("python", info)
        self.assertEqual(info["anki"]["state"], "deckBrowser")
        self.assertEqual(info["anki"]["profile"], "Test User")
        self.assertEqual(info["anki"]["mediaDir"], "/tmp/collection.media")
        self.assertIn("sdkPaths", info)
        self.assertNotIn("safety", info)

    def test_command_catalog_is_valid_and_unique(self):
        errors = validate_command_catalog()
        catalog = command_catalog()

        self.assertEqual(errors, [])
        self.assertEqual(len({entry.name for entry in catalog}), len(catalog))
        self.assertGreater(len(catalog), 0)
        self.assertTrue(all(entry.name.startswith("anki_") for entry in catalog))
        self.assertTrue(all(".dev." not in entry.name for entry in catalog))

    def test_addon_menu_exposes_onboarding_and_management_only(self):
        source = (ADDON / "deckhand" / "addon.py").read_text(encoding="utf-8")

        self.assertIn('QMenu("Deckhand", mw)', source)
        self.assertIn('QAction("Onboarding", mw)', source)
        self.assertIn("onboarding_action.triggered.connect(show_onboarding)", source)
        self.assertIn("welcome.show_onboarding(mw, open_setup=show_management, logger=_log)", source)
        self.assertIn('QAction("Management", mw)', source)
        # The developer panel stays reachable from Management's footer button,
        # but no longer gets its own top-level menu entry.
        self.assertNotIn('QAction("Developer Panel", mw)', source)
        self.assertNotIn("show_developer_panel", source)
        self.assertNotIn('QAction("Bridge Status", mw)', source)
        self.assertNotIn("def show_bridge_status", source)
        self.assertNotIn("_show_text_dialog", source)
        self.assertNotIn('QMenu("Developer", mw)', source)
        self.assertNotIn("deckhand_developer_menu", source)
        self.assertNotIn('QAction("Lens Inspector", mw)', source)
        self.assertNotIn("developer_tools_action.triggered.connect(show_developer_tools)", source)
        self.assertNotIn('QAction("Developer Tools", mw)', source)
        self.assertNotIn('QAction("Diagnostics", mw)', source)
        self.assertNotIn("def show_diagnostics", source)
        self.assertNotIn("def diagnostics", source)
        self.assertNotIn('QAction("Open Sidebar", mw)', source)
        self.assertNotIn("DEFAULT_DESKTOP_EMBED_URL", source)
        self.assertNotIn("DECKHAND_DESKTOP_EMBED_URL", source)
        self.assertNotIn("MOCHIBAR_DESKTOP_EMBED_URL", source)
        self.assertNotIn("startChatTurn", source)
        self.assertNotIn("interruptChatTurn", source)

    def test_addon_no_longer_loads_anki_lens_feature(self):
        source = (ADDON / "deckhand" / "addon.py").read_text(encoding="utf-8")

        self.assertNotIn("from . import anki_lens", source)
        self.assertNotIn('anki_lens.setup("deckhand")', source)

    def test_addon_no_longer_uses_fake_runtime_note_store(self):
        source = (ADDON / "deckhand" / "addon.py").read_text(encoding="utf-8")

        self.assertNotIn("_fallback_store", source)
        self.assertNotIn("FakeNoteStore", source)
        self.assertNotIn("Deckhand prototype", source)
        self.assertIn("anki_collection_unavailable", source)

    def test_addon_lazy_loads_runtime_snippets(self):
        source = (ADDON / "deckhand" / "addon.py").read_text(encoding="utf-8")
        module_imports = source.split("def _register_default_tools", 1)[0]

        self.assertNotIn("dev_tools", source)
        self.assertNotIn("runtime_snippets", module_imports)
        self.assertIn('_executor.register("anki_run_python", _run_python_snippet)', source)
        self.assertIn("from . import runtime_snippets", source)

    def test_addon_no_longer_installs_qwebchannel_sidebar_bridge(self):
        source = (ADDON / "deckhand" / "addon.py").read_text(encoding="utf-8")

        self.assertNotIn("def show_sidebar()", source)
        self.assertNotIn("def _make_sidebar_widget()", source)
        self.assertNotIn("deckhand_sidebar", source)
        self.assertNotIn("QDockWidget", source)
        self.assertNotIn("_web_channel = QWebChannel", source)
        self.assertNotIn('registerObject("deckhandBridge"', source)
        self.assertNotIn('_log("sidebar.webengine_loaded")', source)
        self.assertNotIn("desktop React embed is the product UI source of truth", source)

    def test_addon_removed_codex_and_sidebar_smoke_tools(self):
        source = (ADDON / "deckhand" / "addon.py").read_text(encoding="utf-8")

        self.assertNotIn("deckhandLoadFinished", source)
        self.assertNotIn("_on_sidebar_load_timeout", source)
        self.assertNotIn("_show_sidebar_load_failure", source)
        self.assertNotIn("codex.", source)
        self.assertNotIn("codex_app_server", source)
        self.assertNotIn("run_sidebar_interrupt_smoke", source)

    def test_manager_and_projects_menu_paths_are_removed(self):
        source = (ADDON / "deckhand" / "addon.py").read_text(encoding="utf-8")

        self.assertNotIn('QAction("Open Desktop App", mw)', source)
        self.assertNotIn("show_desktop_app_guidance", source)
        self.assertNotIn("def show_manager()", source)
        self.assertNotIn("def show_projects()", source)
        self.assertNotIn('QAction("Manager", mw)', source)
        self.assertNotIn('QAction("Projects", mw)', source)
        self.assertNotIn('_show_web_window("manager"', source)
        self.assertNotIn('_show_web_window("projects"', source)
        self.assertNotIn("def _show_web_window", source)
        self.assertNotIn("QWebEngineView", source)

    def test_management_menu_and_cdp_banner_are_registered(self):
        source = (ADDON / "deckhand" / "addon.py").read_text(encoding="utf-8")
        management_source = (ADDON / "deckhand" / "management.py").read_text(encoding="utf-8")

        self.assertIn('QAction("Management", mw)', source)
        self.assertIn("management_action.triggered.connect(show_management)", source)
        self.assertNotIn("def show_developer_tools() -> None:", source)
        self.assertIn("management.maybe_show_cdp_banner(mw, logger=_log)", source)
        self.assertIn("def show_management(initial_client: str | None = None) -> None:", source)
        self.assertIn("QToolBar", management_source)
        self.assertIn("mw.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)", management_source)
        self.assertNotIn("QDockWidget", management_source)

    def test_management_connect_tab_imports_qt_for_scrollbar_policy(self):
        management_source = (ADDON / "deckhand" / "management.py").read_text(encoding="utf-8")
        connect_tab_source = management_source.split("def _build_connect_tab", 1)[1].split(
            "def _build_status_tab", 1
        )[0]

        self.assertIn("Qt,", connect_tab_source)
        self.assertIn("Qt.ScrollBarPolicy.ScrollBarAlwaysOff", connect_tab_source)

    def test_connect_tab_ready_pill_reflects_runtime_state(self):
        management_source = (ADDON / "deckhand" / "management.py").read_text(encoding="utf-8")
        connect_tab_source = management_source.split("def _build_connect_tab", 1)[1].split(
            "def _build_status_tab", 1
        )[0]

        # The pill must be derived from companion/bridge state, never hardcoded.
        self.assertNotIn('_status_pill("Ready", "ok")', connect_tab_source)
        self.assertIn('"Ready" if connected else "Needs attention"', connect_tab_source)

    def test_management_restart_command_sets_qtwebengine_debug_port(self):
        original = os.environ.get("DECKHAND_ANKI_EXECUTABLE")
        os.environ["DECKHAND_ANKI_EXECUTABLE"] = "/Applications/Anki.app/Contents/MacOS/launcher"
        try:
            with mock.patch.object(management.platform, "system", lambda: "Darwin"):
                command = management.restart_command(9333)
        finally:
            if original is None:
                os.environ.pop("DECKHAND_ANKI_EXECUTABLE", None)
            else:
                os.environ["DECKHAND_ANKI_EXECUTABLE"] = original

        joined = " ".join(command)
        self.assertIn("QTWEBENGINE_REMOTE_DEBUGGING=9333", joined)
        self.assertIn("launcher", joined)
        self.assertIn("pkill -f 'aqt.run", joined)
        # Must poll for the old process to exit, not sleep a fixed amount:
        # the launcher reuses a still-running instance otherwise.
        self.assertIn("for _ in $(seq 1 30)", joined)
        self.assertNotIn("sleep 2", joined)

    def test_management_windows_restart_command_sets_qtwebengine_debug_port(self):
        original = os.environ.get("DECKHAND_ANKI_EXECUTABLE")
        original_state_root = os.environ.get("DECKHAND_ANKI_EXTENSION_STATE_ROOT")
        with tempfile.TemporaryDirectory() as temp_dir:
            os.environ["DECKHAND_ANKI_EXECUTABLE"] = r"C:\Program Files\Anki\anki.exe"
            os.environ["DECKHAND_ANKI_EXTENSION_STATE_ROOT"] = temp_dir
            try:
                with mock.patch.object(management.platform, "system", lambda: "Windows"):
                    command = management.restart_command(9444, wait_for_pid=12345)
            finally:
                if original is None:
                    os.environ.pop("DECKHAND_ANKI_EXECUTABLE", None)
                else:
                    os.environ["DECKHAND_ANKI_EXECUTABLE"] = original
                if original_state_root is None:
                    os.environ.pop("DECKHAND_ANKI_EXTENSION_STATE_ROOT", None)
                else:
                    os.environ["DECKHAND_ANKI_EXTENSION_STATE_ROOT"] = original_state_root

            script_path = Path(temp_dir) / "logs" / "anki-cdp-restart.cmd"
            vbs_path = Path(temp_dir) / "logs" / "anki-cdp-restart-hidden.vbs"
            log_path = Path(temp_dir) / "logs" / "anki-cdp-restart.log"
            script = script_path.read_text(encoding="utf-8")
            vbs = vbs_path.read_text(encoding="utf-8")
            log = log_path.read_text(encoding="utf-8")

        joined = " ".join(command)
        self.assertEqual(command[0], "schtasks.exe")
        self.assertIn("/create", command)
        self.assertIn("--deckhand-run-task", command)
        self.assertIn("DeckhandAnkiCdpRestart-12345", command)
        self.assertIn("wscript.exe", joined)
        self.assertIn("anki-cdp-restart-hidden.vbs", joined)
        self.assertNotIn("cmd.exe /d /c", joined)
        self.assertIn("anki-cdp-restart.cmd", vbs)
        self.assertIn("WScript.Shell", vbs)
        self.assertIn(", 0, False", vbs)
        self.assertIn("QTWEBENGINE_REMOTE_DEBUGGING=%PORT%", script)
        self.assertIn(r"C:\Program Files\Anki\anki.exe", script)
        self.assertIn("PID eq %PARENT_PID%", script)
        self.assertIn("IMAGENAME eq anki.exe", script)
        self.assertIn("restarter started from Task Scheduler", script)
        self.assertIn("DeckhandAnkiCdpRestart-12345.lock", script)
        self.assertIn('mkdir "%LOCK_DIR%"', script)
        self.assertIn("duplicate restarter ignored", script)
        self.assertIn('rmdir "%LOCK_DIR%"', script)
        self.assertIn('schtasks.exe /delete /tn "%TASK_NAME%" /f', script)
        self.assertIn("ping -n 2 127.0.0.1 >nul", script)
        self.assertIn('del "%VBS_PATH%"', script)
        self.assertNotIn("timeout /t", script)
        self.assertIn("anki-cdp-restart.log", script)
        self.assertIn("start", script)
        self.assertIn("prepared restart worker task DeckhandAnkiCdpRestart-12345 for pid 12345 on port 9444", log)
        self.assertIn("scheduled hidden Task Scheduler handoff DeckhandAnkiCdpRestart-12345", log)
        self.assertNotIn("powershell", joined.lower())
        self.assertNotIn("Stop-Process", joined)
        self.assertIn("/sd", command)
        self.assertIn("12/31/2099", command)
        self.assertIn("/st", command)
        self.assertIn("23:59", command)
        self.assertEqual(script.count('schtasks.exe /delete /tn "%TASK_NAME%" /f'), 1)

    def test_windows_restart_validates_executable_before_scheduling(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing-anki.exe"
            with mock.patch.dict(os.environ, {"DECKHAND_ANKI_EXECUTABLE": str(missing)}):
                with mock.patch.object(management.platform, "system", lambda: "Windows"):
                    with mock.patch.object(management, "_run_windows_restart_scheduler") as schedule:
                        result = management.restart_anki_with_cdp(9444)

        self.assertFalse(result["ok"])
        self.assertIn("could not find Anki", result["detail"])
        schedule.assert_not_called()

    def test_windows_restart_schedules_restarter_then_requests_graceful_close(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "anki.exe"
            executable.write_text("", encoding="utf-8")
            state_root = Path(temp_dir) / "deckhand-state"
            with mock.patch.dict(
                os.environ,
                {"DECKHAND_ANKI_EXECUTABLE": str(executable), "DECKHAND_ANKI_EXTENSION_STATE_ROOT": str(state_root)},
            ):
                with mock.patch.object(management.platform, "system", lambda: "Windows"):
                    with mock.patch.object(management, "_run_windows_restart_scheduler") as schedule:
                        with mock.patch.object(management, "_request_anki_close_for_restart", return_value={"ok": True}) as close:
                            result = management.restart_anki_with_cdp(9555)

        self.assertTrue(result["ok"])
        self.assertEqual(result["command"][0], "schtasks.exe")
        self.assertIn("anki-cdp-restart-hidden.vbs", " ".join(result["command"]))
        schedule.assert_called_once()
        close.assert_called_once()

    def test_windows_restart_reports_close_request_failure(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            executable = Path(temp_dir) / "anki.exe"
            executable.write_text("", encoding="utf-8")
            with mock.patch.dict(os.environ, {"DECKHAND_ANKI_EXECUTABLE": str(executable)}):
                with mock.patch.object(management.platform, "system", lambda: "Windows"):
                    with mock.patch.object(management, "_run_windows_restart_scheduler"):
                        with mock.patch.object(
                            management,
                            "_request_anki_close_for_restart",
                            return_value={"ok": False, "detail": "close unavailable"},
                        ):
                            result = management.restart_anki_with_cdp(9555)

        self.assertFalse(result["ok"])
        self.assertIn("close unavailable", result["detail"])

    def test_windows_restart_scheduler_creates_and_runs_task(self):
        command = [
            "schtasks.exe",
            "/create",
            "/tn",
            "DeckhandAnkiCdpRestart-123",
            "/tr",
            '"cmd.exe" /d /c "C:\\Temp\\anki-cdp-restart.cmd"',
            "/sc",
            "once",
            "/st",
            "12:34",
            "/f",
            "--deckhand-run-task",
            "DeckhandAnkiCdpRestart-123",
        ]

        with mock.patch.object(management.subprocess, "run") as run:
            run.return_value = SimpleNamespace(returncode=0, stdout="", stderr="")
            management._run_windows_restart_scheduler(command)

        self.assertEqual(run.call_count, 2)
        self.assertEqual(run.call_args_list[0].args[0], command[: command.index("--deckhand-run-task")])
        self.assertEqual(
            run.call_args_list[1].args[0],
            ["schtasks.exe", "/run", "/tn", "DeckhandAnkiCdpRestart-123"],
        )

    def test_windows_restart_scheduler_deletes_task_when_run_fails(self):
        command = [
            "schtasks.exe",
            "/create",
            "/tn",
            "DeckhandAnkiCdpRestart-123",
            "/tr",
            '"wscript.exe" //B //Nologo "C:\\Temp\\anki-cdp-restart-hidden.vbs"',
            "/sc",
            "once",
            "/st",
            "12:34",
            "/f",
            "--deckhand-run-task",
            "DeckhandAnkiCdpRestart-123",
        ]

        with mock.patch.object(management.subprocess, "run") as run:
            run.side_effect = [
                SimpleNamespace(returncode=0, stdout="", stderr=""),
                SimpleNamespace(returncode=1, stdout="", stderr="run failed"),
                SimpleNamespace(returncode=0, stdout="", stderr=""),
            ]
            with self.assertRaises(management.subprocess.SubprocessError):
                management._run_windows_restart_scheduler(command)

        self.assertEqual(run.call_count, 3)
        self.assertEqual(
            run.call_args_list[2].args[0],
            ["schtasks.exe", "/delete", "/tn", "DeckhandAnkiCdpRestart-123", "/f"],
        )

    def test_management_uses_anki_launcher_by_default(self):
        path = management.anki_executable_path()

        self.assertEqual(path.name, "launcher")

    def test_management_snapshot_reports_cdp_and_bridge(self):
        original_runtime_status = management.companion.runtime_status
        management.companion.runtime_status = lambda: {
            "state": "running",
            "detail": "test companion",
            "ownedByAnki": True,
            "pid": 123,
            "binary": "/tmp/deckhand-server",
            "url": "http://127.0.0.1:28765",
            "bridgeUrl": "ws://127.0.0.1:28765/ws/anki",
            "health": {"healthy": True},
        }
        try:
            snapshot = management.management_snapshot(["anki_run_python"])
        finally:
            management.companion.runtime_status = original_runtime_status

        self.assertEqual(snapshot["cdp"]["port"], management.DEFAULT_CDP_PORT)
        self.assertEqual(snapshot["companion"]["runtime"]["pid"], 123)
        self.assertNotIn("features", snapshot)
        self.assertIn("ankiBridge", snapshot["companion"])
        self.assertEqual(snapshot["ankiTools"], ["anki_run_python"])
        self.assertEqual(snapshot["toolCount"], 1)

    def test_companion_restores_executable_bit_before_launch(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            binary = Path(temp_dir) / "deckhand-server"
            binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            binary.chmod(0o644)
            self.assertFalse(os.access(binary, os.X_OK))

            companion.ensure_executable(binary)

            self.assertTrue(os.access(binary, os.X_OK))
        source = (ADDON / "deckhand" / "companion.py").read_text(encoding="utf-8")
        self.assertIn("ensure_executable(binary)", source[source.index("def start_companion") :])

    def test_companion_uses_bundled_server_path_for_platform(self):
        path = companion.bundled_server_path()

        self.assertEqual(path.name, companion.SERVER_BINARY)
        self.assertIn(companion.platform_tag(), path.as_posix())

    def test_companion_prepares_versioned_runtime_server_binary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source = Path(temp_dir) / "source" / companion.SERVER_BINARY
            source.parent.mkdir()
            source.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"DECKHAND_ANKI_EXTENSION_STATE_ROOT": str(Path(temp_dir) / "state")}):
                prepared = companion.prepare_runtime_server_binary(source)
                prepared_text = prepared.read_text(encoding="utf-8")

            self.assertEqual(prepared.name, companion.SERVER_BINARY)
            self.assertIn(ADDON_VERSION, prepared.parts)
            self.assertIn(companion.platform_tag(), prepared.parts)
            self.assertNotEqual(prepared, source)
            self.assertEqual(prepared_text, "#!/bin/sh\nexit 0\n")

    def test_companion_binary_override_skips_runtime_copy(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            configured = Path(temp_dir) / companion.SERVER_BINARY
            configured.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            with mock.patch.dict(os.environ, {"DECKHAND_COMPANION_BINARY": str(configured)}):
                prepared = companion.prepare_runtime_server_binary()

        self.assertEqual(prepared, configured)

    def test_companion_owner_file_records_addon_version_and_binary(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            binary = Path(temp_dir) / companion.SERVER_BINARY
            with mock.patch.dict(os.environ, {"DECKHAND_COMPANION_OWNER_FILE": str(Path(temp_dir) / "owner.json")}):
                companion.write_owner_file(1234, binary)
                owner = companion.read_owner_file()

        self.assertEqual(owner["pid"], 1234)
        self.assertEqual(owner["addonVersion"], ADDON_VERSION)
        self.assertEqual(owner["binary"], str(binary))

    def test_companion_keeps_current_recorded_owner(self):
        original_stop_companion_pid = companion.stop_companion_pid
        calls = []
        with tempfile.TemporaryDirectory() as temp_dir:
            owner_path = Path(temp_dir) / "owner.json"
            pid_path = Path(temp_dir) / "companion.pid"
            owner_path.write_text(json.dumps({"addonVersion": ADDON_VERSION, "pid": 1234}), encoding="utf-8")
            pid_path.write_text("1234", encoding="utf-8")
            companion.stop_companion_pid = lambda pid, logger=None, timeout=2.0, owned=False: calls.append(pid)
            try:
                with mock.patch.dict(
                    os.environ,
                    {
                        "DECKHAND_COMPANION_OWNER_FILE": str(owner_path),
                        "DECKHAND_COMPANION_PID_FILE": str(pid_path),
                    },
                ):
                    status = companion.stop_stale_recorded_companion()
            finally:
                companion.stop_companion_pid = original_stop_companion_pid

        self.assertEqual(status["state"], "current")
        self.assertEqual(calls, [])

    def test_companion_stops_recorded_owner_from_previous_addon_version(self):
        original_stop_companion_pid = companion.stop_companion_pid
        original_process_alive = companion.process_alive
        original_health_status = companion.health_status
        calls = []
        with tempfile.TemporaryDirectory() as temp_dir:
            owner_path = Path(temp_dir) / "owner.json"
            pid_path = Path(temp_dir) / "companion.pid"
            owner_path.write_text(json.dumps({"addonVersion": "0.1.7", "pid": 1234}), encoding="utf-8")
            pid_path.write_text("1234", encoding="utf-8")
            companion.stop_companion_pid = lambda pid, logger=None, timeout=2.0, owned=False: calls.append((pid, owned)) or {
                "state": "stopped"
            }
            companion.process_alive = lambda pid: True
            companion.health_status = lambda: {"healthy": True, "service": companion.EXPECTED_SERVICE}
            try:
                with mock.patch.dict(
                    os.environ,
                    {
                        "DECKHAND_COMPANION_OWNER_FILE": str(owner_path),
                        "DECKHAND_COMPANION_PID_FILE": str(pid_path),
                    },
                ):
                    status = companion.stop_stale_recorded_companion()
            finally:
                companion.stop_companion_pid = original_stop_companion_pid
                companion.process_alive = original_process_alive
                companion.health_status = original_health_status

        self.assertEqual(status["state"], "stopped")
        self.assertEqual(calls, [(1234, True)])

    def test_companion_skips_stale_stop_for_unverified_process(self):
        original_stop_companion_pid = companion.stop_companion_pid
        original_process_alive = companion.process_alive
        original_health_status = companion.health_status
        calls = []
        with tempfile.TemporaryDirectory() as temp_dir:
            owner_path = Path(temp_dir) / "owner.json"
            pid_path = Path(temp_dir) / "companion.pid"
            owner_path.write_text(json.dumps({"addonVersion": "0.1.7", "pid": 1234}), encoding="utf-8")
            pid_path.write_text("1234", encoding="utf-8")
            companion.stop_companion_pid = lambda pid, logger=None, timeout=2.0, owned=False: calls.append(pid)
            companion.process_alive = lambda pid: True
            companion.health_status = lambda: {"healthy": False, "error": "connection refused"}
            try:
                with mock.patch.dict(
                    os.environ,
                    {
                        "DECKHAND_COMPANION_OWNER_FILE": str(owner_path),
                        "DECKHAND_COMPANION_PID_FILE": str(pid_path),
                    },
                ):
                    status = companion.stop_stale_recorded_companion()
            finally:
                companion.stop_companion_pid = original_stop_companion_pid
                companion.process_alive = original_process_alive
                companion.health_status = original_health_status

            self.assertFalse(owner_path.exists())

        self.assertEqual(status["state"], "skipped")
        self.assertEqual(calls, [])

    def test_companion_clears_stale_owner_when_process_is_gone(self):
        original_process_alive = companion.process_alive
        with tempfile.TemporaryDirectory() as temp_dir:
            owner_path = Path(temp_dir) / "owner.json"
            pid_path = Path(temp_dir) / "companion.pid"
            owner_path.write_text(json.dumps({"addonVersion": "0.1.7", "pid": 1234}), encoding="utf-8")
            pid_path.write_text("1234", encoding="utf-8")
            companion.process_alive = lambda pid: False
            try:
                with mock.patch.dict(
                    os.environ,
                    {
                        "DECKHAND_COMPANION_OWNER_FILE": str(owner_path),
                        "DECKHAND_COMPANION_PID_FILE": str(pid_path),
                    },
                ):
                    status = companion.stop_stale_recorded_companion()
            finally:
                companion.process_alive = original_process_alive

            self.assertFalse(owner_path.exists())
            self.assertFalse(pid_path.exists())

        self.assertEqual(status["state"], "stopped")

    def test_companion_health_treats_version_mismatch_as_stale(self):
        class FakeResponse:
            def __init__(self, payload):
                self._body = json.dumps(payload).encode("utf-8")

            def read(self):
                return self._body

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        original_urlopen = companion.urlopen
        try:
            companion.urlopen = lambda url, timeout=0.3: FakeResponse(
                {"ready": True, "service": companion.EXPECTED_SERVICE, "version": "0.1.0"}
            )
            stale = companion.health_status()
            companion.urlopen = lambda url, timeout=0.3: FakeResponse(
                {"ready": True, "service": companion.EXPECTED_SERVICE, "version": ADDON_VERSION}
            )
            current = companion.health_status()
        finally:
            companion.urlopen = original_urlopen

        self.assertTrue(stale["healthy"])
        self.assertFalse(stale["compatible"])
        self.assertEqual(stale["staleReasons"], ["version_mismatch"])
        self.assertTrue(current["healthy"])
        self.assertTrue(current["compatible"])
        self.assertEqual(current["staleReasons"], [])

    def test_companion_prunes_runtime_binaries_from_other_versions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.dict(os.environ, {"DECKHAND_ANKI_EXTENSION_STATE_ROOT": str(Path(temp_dir) / "state")}):
                old_dir = companion.default_runtime_dir() / "bin" / "0.0.1" / companion.platform_tag()
                old_dir.mkdir(parents=True)
                (old_dir / companion.SERVER_BINARY).write_text("old", encoding="utf-8")
                source = Path(temp_dir) / "source" / companion.SERVER_BINARY
                source.parent.mkdir()
                source.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")

                prepared = companion.prepare_runtime_server_binary(source)

                self.assertTrue(prepared.exists())
                self.assertFalse(old_dir.exists())

    def test_companion_runtime_file_candidates_include_legacy_dirs(self):
        with mock.patch.dict(os.environ):
            os.environ.pop("DECKHAND_COMPANION_PID_FILE", None)
            os.environ.pop("DECKHAND_ANKI_EXTENSION_STATE_ROOT", None)
            candidates = companion._runtime_file_candidates("DECKHAND_COMPANION_PID_FILE", "companion.pid")
            primary = companion.default_runtime_dir() / "companion.pid"
            state_legacy = state_paths.work_root() / "runtime" / "companion.pid"
            pre_018_legacy = Path.home() / "Library" / "Application Support" / "Deckhand" / "runtime" / "companion.pid"

        self.assertEqual(candidates[0], primary)
        self.assertIn(state_legacy, candidates)
        self.assertIn(pre_018_legacy, candidates)

    def test_companion_ensure_running_defers_to_stopping_upgrade(self):
        original_stop_stale = companion.stop_stale_recorded_companion
        companion.stop_stale_recorded_companion = lambda logger=None: {"state": "stopping", "pid": 1234}
        try:
            status = companion.ensure_running()
        finally:
            companion.stop_stale_recorded_companion = original_stop_stale

        self.assertEqual(status["state"], "stopping")
        self.assertEqual(status["pid"], 1234)

    def test_runtime_root_stays_out_of_roaming_profile_on_windows(self):
        home = Path("/Users/example")
        with mock.patch.dict(
            os.environ,
            {"LOCALAPPDATA": "/win/Local", "APPDATA": "/win/Roaming"},
        ):
            runtime = state_paths.default_runtime_root(home=home, system="windows")
            state = state_paths.default_state_root(home=home, system="windows")

        self.assertEqual(runtime, Path("/win/Local") / "Deckhand" / "state")
        self.assertEqual(state, Path("/win/Roaming") / "Deckhand" / "state")
        self.assertEqual(
            state_paths.default_runtime_root(home=home, system="darwin"),
            state_paths.default_state_root(home=home, system="darwin"),
        )

    def test_companion_ensure_running_reuses_healthy_server(self):
        original_health_status = companion.health_status
        original_started_pid = companion._started_pid
        original_stop_stale = companion.stop_stale_recorded_companion
        companion._started_pid = 4321
        companion.health_status = lambda: {"healthy": True, "version": "0.1.0"}
        companion.stop_stale_recorded_companion = lambda logger=None: {"state": "not_owned"}
        try:
            status = companion.ensure_running()
        finally:
            companion.health_status = original_health_status
            companion._started_pid = original_started_pid
            companion.stop_stale_recorded_companion = original_stop_stale

        self.assertEqual(status["state"], "running")
        self.assertEqual(status["pid"], 4321)
        self.assertTrue(status["ownedByAnki"])

    def test_companion_token_is_generated_once_and_exported(self):
        original = os.environ.get("DECKHAND_COMPANION_TOKEN")
        original_cached = companion._companion_token
        os.environ.pop("DECKHAND_COMPANION_TOKEN", None)
        companion._companion_token = None
        try:
            first = companion.companion_token()
            second = companion.companion_token()
        finally:
            companion._companion_token = original_cached
            if original is None:
                os.environ.pop("DECKHAND_COMPANION_TOKEN", None)
            else:
                os.environ["DECKHAND_COMPANION_TOKEN"] = original

        self.assertEqual(first, second)
        self.assertGreater(len(first), 24)

    def test_companion_ensure_running_starts_missing_server(self):
        class FakeProcess:
            pid = 9876

        calls = []
        original_health_status = companion.health_status
        original_bundled_server_path = companion.bundled_server_path
        original_prepare_runtime_server_binary = companion.prepare_runtime_server_binary
        original_start_companion = companion.start_companion
        original_stop_stale = companion.stop_stale_recorded_companion
        with tempfile.TemporaryDirectory() as temp_dir:
            binary = Path(temp_dir) / companion.SERVER_BINARY
            runtime_binary = Path(temp_dir) / "runtime" / companion.SERVER_BINARY
            binary.write_text("#!/bin/sh\n", encoding="utf-8")
            checks = iter(
                [
                    {"healthy": False, "error": "connection refused"},
                    {"healthy": True, "version": "0.1.0"},
                ]
            )
            companion.health_status = lambda: next(checks, {"healthy": True, "version": "0.1.0"})
            companion.bundled_server_path = lambda: binary
            companion.prepare_runtime_server_binary = lambda source=None: runtime_binary
            companion.start_companion = lambda path, logger=None: calls.append(path) or FakeProcess()
            companion.stop_stale_recorded_companion = lambda logger=None: {"state": "not_owned"}
            try:
                status = companion.ensure_running()
            finally:
                companion.health_status = original_health_status
                companion.bundled_server_path = original_bundled_server_path
                companion.prepare_runtime_server_binary = original_prepare_runtime_server_binary
                companion.start_companion = original_start_companion
                companion.stop_stale_recorded_companion = original_stop_stale

        self.assertEqual(calls, [runtime_binary])
        self.assertEqual(status["state"], "running")
        self.assertTrue(status["ownedByAnki"])
        self.assertEqual(status["pid"], 9876)

    def test_companion_ensure_running_reports_stale_unowned_server(self):
        original_health_status = companion.health_status
        original_read_pid_file = companion.read_pid_file
        original_stop_recorded_companion = companion.stop_recorded_companion
        original_stop_stale = companion.stop_stale_recorded_companion
        companion.stop_stale_recorded_companion = lambda logger=None: {"state": "not_owned"}
        companion.health_status = lambda: {
            "healthy": True,
            "compatible": False,
            "staleReasons": ["unexpected_service"],
        }
        companion.read_pid_file = lambda: None
        companion.stop_recorded_companion = lambda logger=None: {"state": "not_owned"}
        try:
            status = companion.ensure_running()
        finally:
            companion.health_status = original_health_status
            companion.read_pid_file = original_read_pid_file
            companion.stop_recorded_companion = original_stop_recorded_companion
            companion.stop_stale_recorded_companion = original_stop_stale

        self.assertEqual(status["state"], "stale")
        self.assertFalse(status["ownedByAnki"])
        self.assertEqual(status["health"]["staleReasons"], ["unexpected_service"])

    def test_companion_status_compatibility_checks_service_and_version_only(self):
        original_urlopen = companion.urlopen

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                payload = {
                    "service": "deckhand-anki-companion",
                    "ready": True,
                    "version": ADDON_VERSION,
                    "endpoints": ["/api/codex/session/start"],
                }
                return json.dumps(payload).encode("utf-8")

        companion.urlopen = lambda url, timeout: FakeResponse()
        try:
            status = companion.health_status()
        finally:
            companion.urlopen = original_urlopen

        self.assertTrue(status["healthy"])
        self.assertTrue(status["compatible"])
        self.assertEqual(status["staleReasons"], [])

    def test_addon_setup_ensures_companion_before_safe_bridge(self):
        source = (ADDON / "deckhand" / "addon.py").read_text(encoding="utf-8")
        setup_body = source[source.index("def setup() -> None:") : source.index("def _install_companion_shutdown_hook")]

        self.assertIn("from . import companion", source)
        self.assertLess(setup_body.index("companion.ensure_running"), setup_body.index("_start_safe_bridge_transport()"))
        self.assertIn("profile_will_close", source)
        self.assertIn("companion.stop_started_companion", source)

    def test_management_has_companion_restart_action(self):
        source = (ADDON / "deckhand" / "management.py").read_text(encoding="utf-8")

        self.assertIn('QPushButton("Restart helper")', source)
        self.assertIn("companion.restart_companion", source)
        self.assertIn("management.companion_restart_requested", source)

    def test_management_status_tab_exposes_webengine_restart_action(self):
        source = (ADDON / "deckhand" / "management.py").read_text(encoding="utf-8")
        status_tab_source = source[source.index("def _build_status_tab") : source.index("def _build_server_tab")]

        self.assertIn("webengine_restart_button = QPushButton(BANNER_PRIMARY_ACTION)", status_tab_source)
        self.assertIn("_restart_anki_for_deckhand_from_ui(widget, cdp_status()[\"port\"], logger=logger)", status_tab_source)
        self.assertIn("webengine_restart_button.setEnabled(not bool(cdp[\"open\"]))", status_tab_source)

    def test_qt_symbols_are_imported_in_direct_function_scope(self):
        qt_names = {
            "QAction",
            "QApplication",
            "QButtonGroup",
            "QCheckBox",
            "QDesktopServices",
            "QDialog",
            "QDialogButtonBox",
            "QDrag",
            "QFormLayout",
            "QFrame",
            "QGuiApplication",
            "QHBoxLayout",
            "QLabel",
            "QLineEdit",
            "QListWidget",
            "QListWidgetItem",
            "QMenu",
            "QMessageBox",
            "QMimeData",
            "QPlainTextEdit",
            "QPushButton",
            "QScrollArea",
            "QStackedWidget",
            "QTabWidget",
            "QThread",
            "QTimer",
            "QToolBar",
            "QUrl",
            "QVBoxLayout",
            "QWidget",
            "Qt",
        }
        missing: list[str] = []

        for path in sorted((ADDON / "deckhand").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            module_imports = _qt_import_names(tree.body)
            for node in tree.body:
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                imported = module_imports | _direct_qt_import_names(node)
                used = _direct_qt_name_uses(node, qt_names)
                for name in sorted(used - imported):
                    missing.append(f"{path.name}:{node.name}:{name}")

        self.assertEqual(missing, [])

    def test_cdp_banner_copy_is_user_friendly(self):
        self.assertEqual(management.BANNER_TITLE, "Let Deckhand control Anki")
        self.assertEqual(
            management.BANNER_SUMMARY,
            "Restart once to let Deckhand inspect and operate Anki more reliably.",
        )
        self.assertEqual(management.BANNER_PRIMARY_ACTION, "Restart Anki for Deckhand")
        self.assertEqual(management.BANNER_DISMISS_ACTION, "Not now")
        self.assertIn("local debugging port", management.BANNER_BODY)
        self.assertIn("deck", management.BANNER_BODY)
        self.assertIn("reviewer", management.BANNER_BODY)
        self.assertIn("browser", management.BANNER_BODY)
        self.assertIn("editor", management.BANNER_BODY)
        self.assertNotIn("CDP", management.BANNER_TITLE)
        self.assertNotIn("CDP", management.BANNER_PRIMARY_ACTION)

    def test_cdp_banner_has_collapsed_learn_more_details(self):
        source = (ADDON / "deckhand" / "management.py").read_text(encoding="utf-8")

        self.assertIn('learn_more = QPushButton("Learn more")', source)
        self.assertIn("details.setVisible(False)", source)
        self.assertIn('learn_more.setText("Show less" if expanded else "Learn more")', source)

    def test_cdp_banner_only_shows_when_action_is_needed(self):
        source = (ADDON / "deckhand" / "management.py").read_text(encoding="utf-8")
        banner_body = source[source.index("def maybe_show_cdp_banner") : source.index("def _make_cdp_banner_widget")]

        self.assertIn("if settings.cdp_banner_dismissed():", banner_body)
        self.assertIn("settings.set_cdp_banner_dismissed(False)", banner_body)
        self.assertIn('if status["open"]:', banner_body)
        self.assertNotIn("CONNECTED_TITLE", source)
        self.assertIn("settings.set_cdp_banner_dismissed(True)", source)

    def test_cdp_banner_dismissal_persists_across_sessions(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.dict(os.environ, {"DECKHAND_ANKI_EXTENSION_STATE_ROOT": temp_dir}):
                self.assertFalse(settings.cdp_banner_dismissed())
                management.dismiss_cdp_banner()
                self.assertTrue(settings.cdp_banner_dismissed())
        management._banner_dismissed = False

    def test_cdp_banner_stale_dismissal_is_cleared_when_restart_is_still_needed(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.dict(os.environ, {"DECKHAND_ANKI_EXTENSION_STATE_ROOT": temp_dir}):
                settings.set_cdp_banner_dismissed(True)
                management._banner_dismissed = False
                status = {"host": "127.0.0.1", "port": 9222, "open": False, "url": "", "launchEnv": ""}
                with mock.patch.object(management, "cdp_status", lambda: status):
                    management.maybe_show_cdp_banner(object())
                self.assertFalse(settings.cdp_banner_dismissed())

    def test_lens_inspector_is_removed_from_management(self):
        config = json.loads((ADDON / "config.json").read_text(encoding="utf-8"))
        management_source = (ADDON / "deckhand" / "management.py").read_text(encoding="utf-8")
        management_body = management_source[
            management_source.index("def _build_management_dialog") : management_source.index("def mcp_install_instructions")
        ]

        self.assertNotIn("enable_lens_inspector", config)
        self.assertFalse((ADDON / "deckhand" / "anki_lens").exists())
        self.assertNotIn('QCheckBox("Enable Lens Inspector")', management_body)
        self.assertIn('dialog.setWindowTitle(f"Deckhand {ADDON_VERSION}")', management_body)
        self.assertIn('heading = QLabel(f"Deckhand {ADDON_VERSION}")', management_body)
        self.assertIn('QPushButton("Restart helper")', management_body)
        self.assertIn('_section_title("Capabilities")', management_body)
        self.assertIn('QLineEdit(mcp_url)', management_body)
        self.assertIn('QPushButton("Copy")', management_body)
        self.assertIn("QGuiApplication.clipboard()", management_body)
        self.assertIn('QPushButton("Developer Panel...")', management_body)
        self.assertNotIn("QGroupBox", management_body)
        self.assertNotIn("def show_developer_dialog", management_source)
        self.assertNotIn("anki_lens", management_source)

    def test_management_dialog_has_user_facing_tabs(self):
        source = (ADDON / "deckhand" / "management.py").read_text(encoding="utf-8")

        self.assertIn('tabs.addTab(_build_connect_tab(tabs, logger=logger, initial_client=initial_client), "Connect")', source)
        self.assertIn('tabs.addTab(_build_status_tab(tabs, anki_tools, logger=logger), "Status")', source)
        self.assertIn('tabs.addTab(_build_server_tab(tabs, logger=logger), "Server")', source)
        self.assertIn('tabs.addTab(_build_skills_tab(tabs, logger=logger), "Skills")', source)
        self.assertIn('QPushButton("Test connection")', source)
        self.assertIn('QPushButton("Start helper")', source)
        self.assertIn('QPushButton("Stop helper")', source)
        self.assertIn('QPushButton("Open logs folder")', source)
        self.assertIn('QPushButton("Install skills for Claude Code")', source)

    def test_developer_panel_exposes_effective_tool_inspector(self):
        source = (ADDON / "deckhand" / "management.py").read_text(encoding="utf-8")

        self.assertIn("def show_developer_panel", source)
        self.assertIn('dialog.setWindowTitle("Deckhand Developer Panel")', source)
        self.assertIn('tabs.addTab(_build_connection_tab(tabs, anki_tools), "Connection")', source)
        self.assertIn('tabs.addTab(_build_webengine_tab(tabs, logger=logger), "WebEngine")', source)
        self.assertIn('tabs.addTab(_build_logs_tab(tabs, anki_tools), "Logs")', source)
        self.assertNotIn('tabs.addTab(_build_tools_tab(tabs, anki_tools), "Tools")', source)
        self.assertNotIn('QPushButton("All tools")', source)
        self.assertNotIn('QPushButton("Runtime + WebEngine")', source)
        self.assertNotIn('QPushButton("Save visibility")', source)
        self.assertNotIn("tool_visibility.save_visible_tool_names", source)
        self.assertIn("QPlainTextEdit", source)

    def test_management_ui_exposes_tts_provider_settings(self):
        source = (ADDON / "deckhand" / "management.py").read_text(encoding="utf-8")

        self.assertIn('tabs.addTab(_build_tts_tab(tabs, logger=logger), "TTS")', source)
        self.assertIn('"OpenAI"', source)
        self.assertIn('"Gemini"', source)
        self.assertIn('"xAI / Grok"', source)
        self.assertIn('"ElevenLabs"', source)
        self.assertIn("settings.set_tts_provider_settings", source)
        self.assertIn("field.setEchoMode(QLineEdit.Password)", source)

    def test_tool_view_models_join_live_tools_with_catalog_metadata(self):
        models = management.tool_view_models(["anki_run_python", "anki_runtime_info", "anki_removed_prototype"])
        by_name = {model["name"]: model for model in models}

        self.assertEqual([model["name"] for model in models], ["anki_removed_prototype", "anki_run_python", "anki_runtime_info"])
        self.assertEqual(by_name["anki_run_python"]["namespace"], "anki_run")
        self.assertIn("Run Python inside Anki", by_name["anki_run_python"]["description"])
        self.assertIn("large fields", by_name["anki_run_python"]["description"])
        self.assertIn("resultFilePath", by_name["anki_run_python"]["description"])
        self.assertTrue(by_name["anki_run_python"]["annotations"]["destructiveHint"])
        self.assertTrue(by_name["anki_runtime_info"]["annotations"]["readOnlyHint"])
        self.assertFalse(by_name["anki_runtime_info"]["annotations"]["destructiveHint"])
        self.assertFalse(by_name["anki_removed_prototype"]["known"])
        self.assertEqual(by_name["anki_removed_prototype"]["description"], "No catalog metadata")

    def test_mcp_install_instructions_are_standard_http_only(self):
        instructions = management.mcp_install_instructions("http://127.0.0.1:28765/mcp")

        self.assertIn("http://127.0.0.1:28765/mcp", instructions)
        self.assertIn("Streamable HTTP", instructions)
        self.assertIn("standard", instructions)
        self.assertNotIn("Claude Desktop", instructions)
        self.assertNotIn("Cursor", instructions)
        self.assertNotIn("VS Code", instructions)

    def test_settings_persist_across_reads(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.dict(os.environ, {"DECKHAND_ANKI_EXTENSION_STATE_ROOT": temp_dir}):
                self.assertEqual(settings.companion_port(), settings.DEFAULT_COMPANION_PORT)
                self.assertTrue(settings.companion_autostart())
                self.assertFalse(settings.require_mcp_token())

                settings.set_companion_port(29001)
                settings.set_companion_autostart(False)
                settings.set_require_mcp_token(True)

                self.assertEqual(settings.companion_port(), 29001)
                self.assertFalse(settings.companion_autostart())
                self.assertTrue(settings.require_mcp_token())
                self.assertEqual(settings.set_companion_port(-5), settings.DEFAULT_COMPANION_PORT)

    def test_settings_persistent_token_is_stable_until_regenerated(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.dict(os.environ, {"DECKHAND_ANKI_EXTENSION_STATE_ROOT": temp_dir}):
                first = settings.persistent_token()
                second = settings.persistent_token()
                regenerated = settings.regenerate_persistent_token()

        self.assertEqual(first, second)
        self.assertGreater(len(first), 24)
        self.assertNotEqual(first, regenerated)

    def test_tts_schema_reports_provider_parameters_without_secrets(self):
        payload = tts.schema()
        encoded = json.dumps(payload)

        self.assertEqual(payload["module"], "deckhand.tts")
        self.assertIn("openai", payload["providers"])
        self.assertIn("gemini", payload["providers"])
        self.assertIn("xai", payload["providers"])
        self.assertIn("elevenlabs", payload["providers"])
        self.assertIn("stability", payload["providers"]["elevenlabs"]["properties"])
        self.assertNotIn("apiKey", encoded)
        self.assertNotIn("secret", encoded.lower())

    def test_tts_providers_report_configured_status_without_secret_values(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env = {
                "DECKHAND_ANKI_EXTENSION_STATE_ROOT": temp_dir,
                "DECKHAND_OPENAI_API_KEY": "",
                "DECKHAND_GEMINI_API_KEY": "",
                "DECKHAND_XAI_API_KEY": "",
                "DECKHAND_ELEVENLABS_API_KEY": "",
            }
            with mock.patch.dict(os.environ, env):
                settings.set_tts_provider_settings("openai", {"apiKey": "sk-test-secret"})
                settings.set_tts_provider_settings("elevenlabs", {"apiKey": "eleven-secret"})
                by_name = {provider["name"]: provider for provider in tts.providers()}

        self.assertTrue(by_name["openai"]["configured"])
        self.assertFalse(by_name["elevenlabs"]["configured"])
        self.assertEqual(by_name["elevenlabs"]["missingConfig"], ["voiceId"])
        self.assertNotIn("sk-test-secret", json.dumps(by_name))
        self.assertNotIn("eleven-secret", json.dumps(by_name))

    def test_tts_request_builders_match_provider_contracts(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env = {
                "DECKHAND_ANKI_EXTENSION_STATE_ROOT": temp_dir,
                "DECKHAND_OPENAI_API_KEY": "",
                "DECKHAND_GEMINI_API_KEY": "",
                "DECKHAND_XAI_API_KEY": "",
                "DECKHAND_ELEVENLABS_API_KEY": "",
            }
            with mock.patch.dict(os.environ, env):
                settings.set_tts_provider_settings("openai", {"apiKey": "openai-key"})
                settings.set_tts_provider_settings("gemini", {"apiKey": "gemini-key", "voice": "Puck", "languageCode": "ja-JP", "promptPrefix": "Speak this test:"})
                settings.set_tts_provider_settings("xai", {"apiKey": "xai-key", "voice": "leo", "language": "ja"})
                settings.set_tts_provider_settings("elevenlabs", {"apiKey": "eleven-key", "voiceId": "voice-123"})

                openai = tts.build_request("openai", "hello", {"voice": "shimmer"})
                gemini = tts.build_request("gemini", "hello", {})
                xai = tts.build_request("grok", "hello", {"bit_rate": 64000})
                eleven = tts.build_request("elevenlabs", "hello", {"stability": 0.35, "seed": 42})

        self.assertEqual(openai["body"], {"model": "gpt-4o-mini-tts", "voice": "shimmer", "input": "hello", "response_format": "mp3"})
        self.assertIn("/models/gemini-3.1-flash-tts-preview:generateContent", gemini["url"])
        self.assertEqual(gemini["body"]["contents"][0]["parts"][0]["text"], "Speak this test: hello")
        self.assertEqual(gemini["body"]["generationConfig"]["responseModalities"], ["AUDIO"])
        self.assertEqual(gemini["body"]["generationConfig"]["speechConfig"]["languageCode"], "ja-JP")
        self.assertEqual(gemini["body"]["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"], "Puck")
        self.assertEqual(xai["body"]["voice_id"], "leo")
        self.assertEqual(xai["body"]["language"], "ja")
        self.assertEqual(xai["body"]["output_format"], {"codec": "mp3", "sample_rate": 24000, "bit_rate": 64000})
        self.assertIn("output_format=mp3_44100_128", eleven["url"])
        self.assertEqual(eleven["body"]["voice_settings"]["stability"], 0.35)
        self.assertEqual(eleven["body"]["seed"], 42)

    def test_tts_preview_redacts_auth_headers(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            env = {
                "DECKHAND_ANKI_EXTENSION_STATE_ROOT": temp_dir,
                "DECKHAND_ELEVENLABS_API_KEY": "",
            }
            with mock.patch.dict(os.environ, env):
                settings.set_tts_provider_settings("elevenlabs", {"apiKey": "eleven-secret", "voiceId": "voice-123"})
                preview = tts.preview_request("elevenlabs", text="hello", stability=0.4)

        self.assertEqual(preview["headers"]["xi-api-key"], "[redacted]")
        self.assertEqual(preview["body"]["voice_settings"]["stability"], 0.4)
        self.assertNotIn("eleven-secret", json.dumps(preview))

    def test_tts_wav_from_pcm16_wraps_gemini_audio(self):
        wav = tts.wav_from_pcm16(b"\x01\x00\x02\x00", 24000)

        self.assertEqual(wav[:4], b"RIFF")
        self.assertEqual(wav[8:12], b"WAVE")
        self.assertEqual(wav[24:28], (24000).to_bytes(4, "little"))
        self.assertEqual(wav[-4:], b"\x01\x00\x02\x00")

    def test_runtime_info_exposes_tts_surface_without_provider_keys(self):
        collection = SimpleNamespace(
            sched=SimpleNamespace(version=3),
            media=SimpleNamespace(dir=lambda: "/tmp/collection.media"),
        )
        mw = SimpleNamespace(
            state="deckBrowser",
            col=collection,
            pm=SimpleNamespace(name=lambda: "Test User", base="/tmp/Anki2"),
            addonManager=SimpleNamespace(addonsFolder=lambda: "/tmp/addons21"),
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            env = {
                "DECKHAND_ANKI_EXTENSION_STATE_ROOT": temp_dir,
                "DECKHAND_OPENAI_API_KEY": "",
            }
            with mock.patch.dict(os.environ, env):
                settings.set_tts_provider_settings("openai", {"apiKey": "openai-secret"})
                info = runtime_tools.runtime_info(mw)

        surface = info["deckhand"]["ttsSurface"]
        encoded = json.dumps(surface)
        self.assertEqual(surface["module"], "deckhand.tts")
        self.assertIn("tts.render", surface["usage"])
        self.assertIn("openai", surface["schema"]["providers"])
        self.assertNotIn("openai-secret", encoded)
        self.assertNotIn("apiKey", encoded)

    def test_companion_port_prefers_env_over_settings(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.dict(os.environ, {"DECKHAND_ANKI_EXTENSION_STATE_ROOT": temp_dir}):
                settings.set_companion_port(29002)
                os.environ.pop("DECKHAND_COMPANION_PORT", None)
                self.assertEqual(companion.companion_port(), 29002)
                with mock.patch.dict(os.environ, {"DECKHAND_COMPANION_PORT": "29003"}):
                    self.assertEqual(companion.companion_port(), 29003)

    def test_companion_respects_autostart_setting_unless_forced(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.dict(os.environ, {"DECKHAND_ANKI_EXTENSION_STATE_ROOT": temp_dir}):
                settings.set_companion_autostart(False)
                original_health_status = companion.health_status
                companion.health_status = lambda: {"healthy": False, "error": "connection refused"}
                try:
                    status = companion.ensure_running()
                finally:
                    companion.health_status = original_health_status

        self.assertEqual(status["state"], "disabled")
        self.assertIn("settings", status["detail"])

    def test_companion_start_env_propagates_mcp_token_requirement(self):
        source = (ADDON / "deckhand" / "companion.py").read_text(encoding="utf-8")

        self.assertIn('"DECKHAND_MCP_REQUIRE_TOKEN": "1" if settings.require_mcp_token() else "0"', source)
        self.assertIn("settings.persistent_token()", source)
        self.assertIn("cwd=str(default_runtime_dir())", source)
        self.assertIn('"--parent-pid", str(os.getpid())', source)
        self.assertIn("write_owner_file(_started_pid, binary)", source)
        self.assertIn("stop_stale_recorded_companion(logger=logger)", source)

    def test_companion_process_control_is_windows_safe(self):
        source = (ADDON / "deckhand" / "companion.py").read_text(encoding="utf-8")

        # os.kill is never a liveness probe or terminator on Windows: signal 0
        # would TerminateProcess the target via os.kill's Windows semantics.
        self.assertIn("GetExitCodeProcess", source)
        self.assertIn("PROCESS_QUERY_LIMITED_INFORMATION", source)
        self.assertIn("def terminate_process", source)
        self.assertIn("subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP", source)
        alive_body = source.split("def process_alive", 1)[1].split("def terminate_process", 1)[0]
        self.assertIn("if IS_WINDOWS:", alive_body)
        self.assertIn("_windows_process_alive(pid)", alive_body)

    def test_connect_recipes_cover_target_clients(self):
        url = "http://127.0.0.1:28765/mcp"

        desktop = management.connect_recipe(management.CLIENT_CLAUDE_DESKTOP, url)
        self.assertEqual(desktop["snippet"], url)
        self.assertTrue(any("custom connector" in step for step in desktop["steps"]))

        code = management.connect_recipe(management.CLIENT_CLAUDE_CODE, url)
        self.assertEqual(code["snippet"], connect_hosts.CLAUDE_PLUGIN_INSTALL_COMMANDS)
        self.assertTrue(any(f"claude mcp add --transport http deckhand {url}" in step for step in code["steps"]))
        raw_code = connect_hosts.connect_recipe(management.CLIENT_CLAUDE_CODE, url)
        self.assertEqual(raw_code["steps"][1]["copyText"], connect_hosts.CLAUDE_PLUGIN_INSTALL_COMMANDS)
        self.assertEqual(raw_code["steps"][3]["copyText"], f"claude mcp add --transport http deckhand {url}")

        code_with_token = management.connect_recipe(management.CLIENT_CLAUDE_CODE, url, "tok123")
        self.assertEqual(code_with_token["snippet"], f'claude mcp add --transport http deckhand {url} --header "Authorization: Bearer tok123"')
        self.assertTrue(any(connect_hosts.CLAUDE_PLUGIN_INSTALL_ONE_LINER in step for step in code_with_token["steps"]))

        code_custom_url = management.connect_recipe(management.CLIENT_CLAUDE_CODE, "http://127.0.0.1:9999/mcp")
        self.assertEqual(code_custom_url["snippet"], "claude mcp add --transport http deckhand http://127.0.0.1:9999/mcp")

        codex_plugin = management.connect_recipe(management.CLIENT_CODEX, url)
        self.assertEqual(codex_plugin["snippet"], connect_hosts.CODEX_PLUGIN_INSTALL_COMMANDS)
        self.assertTrue(any(f'url = "{url}"' in step for step in codex_plugin["steps"]))
        raw_codex = connect_hosts.connect_recipe(management.CLIENT_CODEX, url)
        self.assertEqual(raw_codex["steps"][1]["copyText"], connect_hosts.CODEX_PLUGIN_INSTALL_COMMANDS)
        self.assertEqual(raw_codex["steps"][3]["copyText"], f'[mcp_servers.deckhand]\nurl = "{url}"')

        codex = management.connect_recipe(management.CLIENT_CODEX, url, "tok123")
        self.assertIn("[mcp_servers.deckhand]", codex["snippet"])
        self.assertIn(f'url = "{url}"', codex["snippet"])
        self.assertIn("Bearer tok123", codex["snippet"])
        self.assertTrue(any(connect_hosts.CODEX_PLUGIN_INSTALL_ONE_LINER in step for step in codex["steps"]))

        codex_desktop = management.connect_recipe(management.CLIENT_CODEX_DESKTOP, url, "tok123")
        self.assertEqual(codex_desktop["label"], "Codex Desktop")
        self.assertTrue(any("Quit and reopen Codex Desktop" in step for step in codex_desktop["steps"]))
        self.assertIn("Bearer tok123", codex_desktop["snippet"])

        codex_desktop_plugin = management.connect_recipe(management.CLIENT_CODEX_DESKTOP, url)
        self.assertEqual(codex_desktop_plugin["snippet"], connect_hosts.CODEX_PLUGIN_MARKETPLACE_SOURCE)
        self.assertTrue(any("Add marketplace" in step for step in codex_desktop_plugin["steps"]))
        self.assertTrue(any("Deckhand marketplace tab" in step for step in codex_desktop_plugin["steps"]))
        self.assertTrue(any(f'url = "{url}"' in step for step in codex_desktop_plugin["steps"]))
        raw_codex_desktop = connect_hosts.connect_recipe(management.CLIENT_CODEX_DESKTOP, url)
        self.assertEqual(raw_codex_desktop["steps"][1]["copyText"], connect_hosts.CODEX_PLUGIN_MARKETPLACE_SOURCE)
        self.assertEqual(raw_codex_desktop["steps"][4]["copyText"], f'[mcp_servers.deckhand]\nurl = "{url}"')

        other = management.connect_recipe("unknown-client", url)
        self.assertEqual(other["snippet"], url)
        self.assertTrue(any("Streamable HTTP" in step for step in other["steps"]))
        raw_other = connect_hosts.connect_recipe("unknown-client", url)
        self.assertEqual(raw_other["steps"][2]["copyText"], url)

    def test_claude_code_plugin_manifest_matches_recipe_endpoint(self):
        manifest = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["name"], "deckhand")
        server = manifest["mcpServers"]["deckhand"]
        self.assertEqual(server["type"], "http")
        self.assertEqual(server["url"], connect_hosts.CLAUDE_PLUGIN_MCP_URL)

    def test_claude_desktop_recipe_leads_with_mcpb_extension(self):
        recipe = management.connect_recipe(management.CLIENT_CLAUDE_DESKTOP, "http://127.0.0.1:28765/mcp", "tok123")

        self.assertIn("Deckhand extension", recipe["steps"][0])
        raw = connect_hosts.connect_recipe(management.CLIENT_CLAUDE_DESKTOP, "http://127.0.0.1:28765/mcp", "tok123")
        self.assertEqual(raw["steps"][0].get("embed"), "mcpb")
        self.assertIn("Use Deckhand and list my Anki decks.", recipe["steps"][2])
        self.assertTrue(any("custom connector" in step for step in recipe["steps"]))
        self.assertTrue(any("access token automatically" in step for step in recipe["steps"]))
        self.assertNotIn("tok123", recipe["snippet"])

    def test_mcpb_bundle_contains_manifest_and_proxy(self):
        from deckhand import mcpb as mcpb_module

        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.dict(os.environ, {"DECKHAND_ANKI_EXTENSION_STATE_ROOT": temp_dir}):
                bundle = mcpb_module.build_bundle(Path(temp_dir) / "Deckhand.mcpb")
                with zipfile.ZipFile(bundle) as archive:
                    names = set(archive.namelist())
                    manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
                    proxy = archive.read("proxy.js").decode("utf-8")

        self.assertEqual(names, {"manifest.json", "proxy.js"})
        self.assertEqual(manifest["manifest_version"], "0.3")
        self.assertEqual(manifest["version"], ADDON_VERSION)
        self.assertEqual(manifest["server"]["type"], "node")
        self.assertIn("${__dirname}/proxy.js", manifest["server"]["mcp_config"]["args"])
        self.assertIn("/mcp", manifest["user_config"]["endpoint"]["default"])
        self.assertTrue(manifest["user_config"]["token"]["sensitive"])
        self.assertIn("DECKHAND_MCP_URL", proxy)
        self.assertIn("mcp-session-id", proxy)

    def test_mcpb_manifest_bakes_token_only_when_required(self):
        from deckhand import mcpb as mcpb_module

        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.dict(os.environ, {"DECKHAND_ANKI_EXTENSION_STATE_ROOT": temp_dir}):
                self.assertEqual(mcpb_module.manifest()["user_config"]["token"]["default"], "")
                settings.set_require_mcp_token(True)
                baked = mcpb_module.manifest()["user_config"]["token"]["default"]
                self.assertEqual(baked, settings.persistent_token())

    def test_shared_recipe_view_offers_mcpb_drag_and_save(self):
        source = (ADDON / "deckhand" / "ui.py").read_text(encoding="utf-8")

        self.assertIn("def make_mcpb_section", source)
        self.assertIn("Install Deckhand in Claude Desktop", source)
        self.assertIn("mime.setUrls([QUrl.fromLocalFile(str(path))])", source)
        self.assertIn('QPushButton("Save extension...")', source)
        # The chip embeds inside the install step instead of trailing the card.
        self.assertIn('step.get("embed") == "mcpb"', source)
        self.assertNotIn("mcpb_section.setVisible", source)
        self.assertIn('step.get("copyText")', source)
        self.assertIn("connect_step_copied", source)
        self.assertIn("copy_row_widget.setVisible(not has_step_copy)", source)

    def test_connect_tab_uses_alphabetical_pill_bar_instead_of_dropdown(self):
        source = (ADDON / "deckhand" / "management.py").read_text(encoding="utf-8")
        ui_source = (ADDON / "deckhand" / "ui.py").read_text(encoding="utf-8")
        connect_body = source[source.index("def _build_connect_tab") : source.index("def _build_status_tab")]
        labels = [host["label"] for host in connect_hosts.connect_hosts()]

        self.assertNotIn("QComboBox", connect_body)
        self.assertNotIn("QComboBox", ui_source)
        self.assertIn("ui.build_host_pillbar", connect_body)
        self.assertIn("ui.build_recipe_view", connect_body)
        self.assertIn("QButtonGroup", ui_source)
        self.assertIn('layout.addWidget(_section_title("Pick your app"))', connect_body)
        self.assertEqual(labels, sorted(labels, key=str.lower))
        self.assertIn("Codex Desktop", labels)

    def test_setup_asset_manifest_reserves_packaged_media_slots(self):
        manifest = connect_hosts.setup_asset_manifest()

        self.assertIn(connect_hosts.CLIENT_CODEX_DESKTOP, manifest)
        self.assertTrue(any(path.endswith("step-1.webp") for path in manifest[connect_hosts.CLIENT_CODEX_DESKTOP]))
        self.assertTrue(any(path.endswith("walkthrough.mp4") for path in manifest[connect_hosts.CLIENT_CLAUDE_DESKTOP]))
        self.assertTrue((ADDON / "deckhand" / "assets" / "setup" / "README.md").is_file())

    def test_connection_checks_map_failures_to_next_actions(self):
        original_health_status = management.companion.health_status
        management.companion.health_status = lambda: {"healthy": False, "error": "connection refused"}
        fake_bridge = SimpleNamespace(to_dict=lambda: {"state": "disconnected", "detail": "no socket"})
        try:
            with mock.patch.object(management, "bridge_status", fake_bridge):
                with mock.patch.object(management, "cdp_status", lambda: {"open": False, "port": 9222}):
                    checks = management.run_connection_checks()
        finally:
            management.companion.health_status = original_health_status

        by_name = {check["name"]: check for check in checks}
        self.assertFalse(by_name["Local helper"]["ok"])
        self.assertIn("Server tab", by_name["Local helper"]["action"])
        self.assertFalse(by_name["Anki bridge"]["ok"])
        self.assertTrue(by_name["WebEngine control"]["optional"])

        report = management.format_connection_checks(checks)
        self.assertIn("[FAIL] Local helper", report)
        self.assertIn("Next:", report)
        self.assertIn("[SKIP] WebEngine control", report)
        self.assertNotIn("Everything required is working", report)

    def test_connection_checks_pass_when_everything_is_healthy(self):
        original_health_status = management.companion.health_status
        management.companion.health_status = lambda: {
            "healthy": True,
            "compatible": True,
            "url": "http://127.0.0.1:28765/status",
        }
        fake_bridge = SimpleNamespace(to_dict=lambda: {"state": "connected", "detail": "paired"})
        try:
            with mock.patch.object(management, "bridge_status", fake_bridge):
                with mock.patch.object(management, "cdp_status", lambda: {"open": True, "port": 9222}):
                    checks = management.run_connection_checks()
        finally:
            management.companion.health_status = original_health_status

        self.assertTrue(all(check["ok"] for check in checks))
        report = management.format_connection_checks(checks)
        self.assertIn("Everything required is working", report)

    def test_skills_install_update_and_user_edit_protection(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_root = Path(temp_dir) / "bundled"
            target_root = Path(temp_dir) / "claude-skills"
            skill_dir = source_root / "dh-test"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                '---\nname: dh-test\ndescription: "Test skill"\n---\n\n# Test\n', encoding="utf-8"
            )

            installed = skills.install_skill(skill_dir, target_root)
            self.assertEqual(installed["status"], skills.STATUS_INSTALLED)
            self.assertTrue((target_root / "dh-test" / "SKILL.md").exists())
            self.assertTrue((target_root / "dh-test" / skills.MANIFEST_FILENAME).exists())

            unchanged = skills.install_skill(skill_dir, target_root)
            self.assertEqual(unchanged["status"], skills.STATUS_UP_TO_DATE)

            (skill_dir / "SKILL.md").write_text("updated source\n", encoding="utf-8")
            updated = skills.install_skill(skill_dir, target_root)
            self.assertEqual(updated["status"], skills.STATUS_UPDATED)

            (target_root / "dh-test" / "SKILL.md").write_text("user edit\n", encoding="utf-8")
            (skill_dir / "SKILL.md").write_text("newer source\n", encoding="utf-8")
            skipped = skills.install_skill(skill_dir, target_root)
            self.assertEqual(skipped["status"], skills.STATUS_SKIPPED_MODIFIED)
            self.assertEqual((target_root / "dh-test" / "SKILL.md").read_text(encoding="utf-8"), "user edit\n")

            forced = skills.install_skill(skill_dir, target_root, force=True)
            self.assertEqual(forced["status"], skills.STATUS_UPDATED)

    def test_skills_never_adopt_unmanaged_directories(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            source_root = Path(temp_dir) / "bundled"
            target_root = Path(temp_dir) / "claude-skills"
            skill_dir = source_root / "dh-test"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text("# Test\n", encoding="utf-8")
            existing = target_root / "dh-test"
            existing.mkdir(parents=True)
            (existing / "SKILL.md").write_text("someone else's skill\n", encoding="utf-8")

            result = skills.install_skill(skill_dir, target_root)

        self.assertEqual(result["status"], skills.STATUS_SKIPPED_UNMANAGED)

    def test_addon_version_is_single_sourced(self):
        manifest = json.loads((ADDON / "manifest.json").read_text(encoding="utf-8"))
        addon_source = (ADDON / "deckhand" / "addon.py").read_text(encoding="utf-8")
        package_source = (ADDON / "deckhand" / "__init__.py").read_text(encoding="utf-8")

        self.assertEqual(manifest["human_version"], ADDON_VERSION)
        self.assertIn('"addonVersion": ADDON_VERSION,', addon_source)
        self.assertNotIn('"addonVersion": "0.', addon_source)
        self.assertIn("from .version import ADDON_VERSION", package_source)
        self.assertIn("__version__ = ADDON_VERSION", package_source)
        self.assertNotIn('__version__ = "0.', package_source)

    def test_companion_server_crate_version_matches_addon(self):
        # The add-on compares the /status version against ADDON_VERSION to
        # detect stale helpers, so the crate must be released in lockstep.
        cargo = (ROOT / "crates" / "deckhand-server" / "Cargo.toml").read_text(encoding="utf-8")
        package_section = cargo.split("[dependencies]", 1)[0]

        self.assertIn(f'version = "{ADDON_VERSION}"', package_section)

    def test_update_version_comparison_handles_tags_and_suffixes(self):
        self.assertTrue(updates.is_newer("0.2.0", "0.1.0"))
        self.assertTrue(updates.is_newer("v1.0.0", "0.9.9"))
        self.assertTrue(updates.is_newer("0.1.1-beta", "0.1.0"))
        self.assertFalse(updates.is_newer("0.1.0", "0.1.0"))
        self.assertFalse(updates.is_newer("0.0.9", "0.1.0"))
        self.assertFalse(updates.is_newer("", "0.1.0"))

    def test_update_check_respects_disabled_and_throttle(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.dict(os.environ, {"DECKHAND_ANKI_EXTENSION_STATE_ROOT": temp_dir}):
                settings.set_update_check_enabled(False)
                self.assertEqual(updates.check_for_update()["reason"], "disabled")

                settings.set_update_check_enabled(True)
                settings.set_last_update_check_ms(int(time.time() * 1000))
                self.assertEqual(updates.check_for_update()["reason"], "throttled")

    def test_update_check_reports_newer_release_and_swallows_errors(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.dict(os.environ, {"DECKHAND_ANKI_EXTENSION_STATE_ROOT": temp_dir}):
                with mock.patch.object(
                    updates,
                    "fetch_latest_release",
                    lambda timeout=5.0: {"tag": "v99.0.0", "url": "https://example.test/release", "name": "v99"},
                ):
                    result = updates.check_for_update(force=True)
                self.assertTrue(result["updateAvailable"])
                self.assertEqual(result["latestVersion"], "99.0.0")
                self.assertEqual(result["url"], "https://example.test/release")

                def boom(timeout=5.0):
                    raise OSError("offline")

                with mock.patch.object(updates, "fetch_latest_release", boom):
                    failed = updates.check_for_update(force=True)
                self.assertTrue(failed["checked"])
                self.assertFalse(failed["updateAvailable"])
                self.assertIn("offline", failed["error"])

    def test_about_info_reports_versions_and_paths(self):
        original_health_status = management.companion.health_status
        management.companion.health_status = lambda: {"healthy": True, "version": "0.1.0"}
        try:
            info = management.about_info()
        finally:
            management.companion.health_status = original_health_status

        self.assertEqual(info["addonVersion"], ADDON_VERSION)
        self.assertEqual(info["companionVersion"], "0.1.0")
        self.assertIn("settings.json", info["settingsPath"])
        self.assertTrue(info["platform"])

    def test_format_diagnostics_is_pasteable_json(self):
        original_health_status = management.companion.health_status
        management.companion.health_status = lambda: {
            "healthy": True,
            "compatible": True,
            "version": "0.1.0",
            "url": "http://127.0.0.1:28765/status",
        }
        fake_bridge = SimpleNamespace(to_dict=lambda: {"state": "connected", "detail": "paired"})
        try:
            with mock.patch.object(management, "bridge_status", fake_bridge):
                with mock.patch.object(management, "cdp_status", lambda: {"open": False, "port": 9222}):
                    diagnostics = management.format_diagnostics()
        finally:
            management.companion.health_status = original_health_status

        payload = json.loads(diagnostics)
        self.assertEqual(payload["about"]["addonVersion"], ADDON_VERSION)
        self.assertIn("companionPort", payload["settings"])
        self.assertEqual(payload["checks"][0]["name"], "Local helper")

    def test_management_dialog_includes_about_tab(self):
        source = (ADDON / "deckhand" / "management.py").read_text(encoding="utf-8")

        self.assertIn('tabs.addTab(_build_about_tab(tabs, logger=logger), "About")', source)
        self.assertIn('QPushButton("Check for updates")', source)
        self.assertIn('QPushButton("Copy diagnostics")', source)

    def test_addon_setup_starts_background_update_check(self):
        source = (ADDON / "deckhand" / "addon.py").read_text(encoding="utf-8")

        self.assertIn("updates.start_background_check(mw, logger=_log)", source)

    def test_default_state_root_is_platform_appropriate(self):
        home = Path("/home/example")

        mac = state_paths.default_state_root(home=home, system="Darwin")
        self.assertEqual(mac, home / "Library" / "Application Support" / "Deckhand" / "state")

        with mock.patch.dict(os.environ, {"APPDATA": "/win/appdata"}):
            windows = state_paths.default_state_root(home=home, system="Windows")
        self.assertEqual(windows, Path("/win/appdata") / "Deckhand" / "state")

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("XDG_DATA_HOME", None)
            linux = state_paths.default_state_root(home=home, system="Linux")
        self.assertEqual(linux, home / ".local" / "share" / "deckhand" / "state")

    def test_state_root_has_no_dev_machine_fallback(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DECKHAND_ANKI_EXTENSION_STATE_ROOT", None)
            root = state_paths.state_root()

        self.assertEqual(root, state_paths.default_state_root())
        source = (ADDON / "deckhand" / "state_paths.py").read_text(encoding="utf-8")
        self.assertNotIn("thoffman", source)
        rust_source = (ROOT / "crates" / "deckhand-server" / "src" / "server_shell.rs").read_text(encoding="utf-8")
        self.assertNotIn("thoffman", rust_source)

    def test_companion_pins_state_root_for_server(self):
        source = (ADDON / "deckhand" / "companion.py").read_text(encoding="utf-8")

        self.assertIn('"DECKHAND_ANKI_EXTENSION_STATE_ROOT": str(work_root())', source)

    def test_ankiweb_installs_skip_github_update_checks(self):
        self.assertTrue(updates.is_ankiweb_install(Path("/addons21/1234567890")))
        self.assertFalse(updates.is_ankiweb_install(Path("/addons21/deckhand")))

        events: list[tuple[str, dict]] = []
        with mock.patch.object(updates, "is_ankiweb_install", lambda package_root=None: True):
            updates.start_background_check(mw=None, logger=lambda event, **payload: events.append((event, payload)))

        self.assertEqual(events, [("updates.check_skipped", {"reason": "ankiweb_install"})])

    def test_about_tab_hides_github_update_button_for_ankiweb_installs(self):
        source = (ADDON / "deckhand" / "management.py").read_text(encoding="utf-8")

        self.assertIn("if updates.is_ankiweb_install():", source)
        self.assertIn("check_button.setVisible(False)", source)
        self.assertIn("Check for Updates", source)

    def test_codex_skills_root_and_install_targets(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DECKHAND_CODEX_SKILLS_DIR", None)
            self.assertEqual(skills.codex_skills_root(), Path.home() / ".codex" / "skills")
        with mock.patch.dict(os.environ, {"DECKHAND_CODEX_SKILLS_DIR": "/tmp/codex-skills"}):
            self.assertEqual(skills.codex_skills_root(), Path("/tmp/codex-skills"))

        target_ids = [target["id"] for target in skills.install_targets()]
        self.assertEqual(target_ids, ["claude_code", "codex"])

    def test_managed_install_roots_only_report_roots_with_manifests(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            claude_root = Path(temp_dir) / "claude"
            codex_root = Path(temp_dir) / "codex"
            managed = claude_root / "dh-test"
            managed.mkdir(parents=True)
            (managed / skills.MANIFEST_FILENAME).write_text("{}", encoding="utf-8")
            unmanaged = codex_root / "someone-elses-skill"
            unmanaged.mkdir(parents=True)
            (unmanaged / "SKILL.md").write_text("# other\n", encoding="utf-8")
            with mock.patch.dict(
                os.environ,
                {"DECKHAND_CLAUDE_SKILLS_DIR": str(claude_root), "DECKHAND_CODEX_SKILLS_DIR": str(codex_root)},
            ):
                roots = skills.managed_install_roots()

        self.assertEqual(roots, [claude_root])

    def test_skills_sync_updates_managed_installs_only(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            bundled = Path(temp_dir) / "bundled" / "dh-test"
            bundled.mkdir(parents=True)
            (bundled / "SKILL.md").write_text("v1\n", encoding="utf-8")
            target_root = Path(temp_dir) / "claude-skills"
            skills.install_skill(bundled, target_root)

            remote_root = Path(temp_dir) / "remote-skills"
            remote_skill = remote_root / "dh-test"
            remote_skill.mkdir(parents=True)
            (remote_skill / "SKILL.md").write_text("v2 from repo\n", encoding="utf-8")
            (remote_root / "dh-brand-new").mkdir()
            (remote_root / "dh-brand-new" / "SKILL.md").write_text("new skill\n", encoding="utf-8")

            outcomes = skills_updates.sync_installed_skills(remote_root, [target_root])

            self.assertEqual([(o["skill"], o["status"]) for o in outcomes], [("dh-test", skills.STATUS_UPDATED)])
            self.assertEqual((target_root / "dh-test" / "SKILL.md").read_text(encoding="utf-8"), "v2 from repo\n")
            self.assertFalse((target_root / "dh-brand-new").exists())

            (target_root / "dh-test" / "SKILL.md").write_text("user edit\n", encoding="utf-8")
            (remote_skill / "SKILL.md").write_text("v3\n", encoding="utf-8")
            outcomes = skills_updates.sync_installed_skills(remote_root, [target_root])
            self.assertEqual(outcomes[0]["status"], skills.STATUS_SKIPPED_MODIFIED)
            self.assertEqual((target_root / "dh-test" / "SKILL.md").read_text(encoding="utf-8"), "user edit\n")

    def test_skills_check_and_sync_respects_disabled_throttle_and_no_installs(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.dict(os.environ, {"DECKHAND_ANKI_EXTENSION_STATE_ROOT": temp_dir}):
                settings.set_skills_auto_update_enabled(False)
                self.assertEqual(skills_updates.check_and_sync()["reason"], "disabled")

                settings.set_skills_auto_update_enabled(True)
                settings.set_last_skills_sync_ms(int(time.time() * 1000))
                self.assertEqual(skills_updates.check_and_sync()["reason"], "throttled")

                settings.set_last_skills_sync_ms(0)
                with mock.patch.object(skills, "managed_install_roots", lambda: []):
                    self.assertEqual(skills_updates.check_and_sync()["reason"], "no_managed_installs")

    def test_skills_tarball_extraction_finds_skills_dir(self):
        import tarfile

        with tempfile.TemporaryDirectory() as temp_dir:
            repo = Path(temp_dir) / "deckhand-skills-main"
            skill = repo / "skills" / "dh-demo"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text("# demo\n", encoding="utf-8")
            tarball = Path(temp_dir) / "repo.tar.gz"
            with tarfile.open(tarball, "w:gz") as archive:
                archive.add(repo, arcname="deckhand-skills-main")

            extract_dir = Path(temp_dir) / "extracted"
            extract_dir.mkdir()
            with tarball.open("rb") as stream:
                skills_dir = skills_updates.extract_skills_dir(stream, extract_dir)

            self.assertTrue((skills_dir / "dh-demo" / "SKILL.md").is_file())

    def test_welcome_shows_only_once_and_can_be_disabled(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            with mock.patch.dict(os.environ, {"DECKHAND_ANKI_EXTENSION_STATE_ROOT": temp_dir}):
                self.assertTrue(welcome.should_show())
                with mock.patch.dict(os.environ, {"DECKHAND_WELCOME_DISABLED": "1"}):
                    self.assertFalse(welcome.should_show())

                events: list[str] = []
                welcome.maybe_show_welcome(None, logger=lambda event, **payload: events.append(event))
                self.assertTrue(settings.welcome_shown())
                self.assertFalse(welcome.should_show())

    def test_addon_setup_wires_welcome_and_skills_sync(self):
        source = (ADDON / "deckhand" / "addon.py").read_text(encoding="utf-8")

        self.assertIn("welcome.maybe_show_welcome(mw, open_setup=show_management, logger=_log)", source)
        self.assertIn("initial_client: str | None = None", source)
        self.assertIn("initial_client=initial_client", source)
        self.assertIn("skills_updates.start_background_sync(mw, logger=_log)", source)

    def test_onboarding_is_a_wizard_with_embedded_guided_setup(self):
        source = (ADDON / "deckhand" / "welcome.py").read_text(encoding="utf-8")

        self.assertIn("QStackedWidget", source)
        self.assertIn("Choose your app", source)
        self.assertIn("connect_hosts.connect_hosts()", source)
        self.assertIn("connect_hosts.CLIENT_CODEX_DESKTOP", source)
        self.assertIn("ui.build_recipe_view", source)
        self.assertIn("Set up voices", source)
        self.assertIn("OpenAI", source)
        self.assertIn("ElevenLabs", source)
        self.assertIn("Provider keys stay in Deckhand settings", source)
        self.assertIn("management.run_connection_checks()", source)
        self.assertIn('open_setup(selected_client["id"])', source)
        self.assertEqual(len(welcome.WIZARD_PAGE_TITLES), 5)

    def test_skills_tab_offers_codex_and_update_check(self):
        source = (ADDON / "deckhand" / "management.py").read_text(encoding="utf-8")

        self.assertIn('QPushButton("Install skills for Codex")', source)
        self.assertIn('QPushButton("Check for skill updates")', source)
        self.assertIn("skills_updates.check_and_sync(force=True)", source)

    def test_format_skill_sync_result_messages(self):
        self.assertIn("Install skills first", management.format_skill_sync_result({"reason": "no_managed_installs"}))
        self.assertIn("offline", management.format_skill_sync_result({"checked": True, "error": "offline"}))
        report = management.format_skill_sync_result(
            {
                "checked": True,
                "updated": 1,
                "outcomes": [{"skill": "dh-test", "status": skills.STATUS_UPDATED}],
            }
        )
        self.assertIn("1 updated", report)
        self.assertIn("dh-test: updated", report)

    def test_bundled_skills_discovery_uses_env_roots_and_frontmatter(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir) / "skills"
            skill_dir = root / "dh-demo"
            skill_dir.mkdir(parents=True)
            (skill_dir / "SKILL.md").write_text(
                '---\nname: dh-demo\ndescription: "Demo description"\n---\n\n# Demo\n', encoding="utf-8"
            )
            (root / "not-a-skill").mkdir()
            with mock.patch.dict(os.environ, {"DECKHAND_BUNDLED_SKILLS_DIRS": str(root)}):
                bundled = skills.bundled_skills()

        self.assertEqual([skill["name"] for skill in bundled], ["dh-demo"])
        self.assertEqual(bundled[0]["description"], "Demo description")

    def test_bridge_transport_frame_helpers_round_trip(self):
        left, right = socket.socketpair()
        try:
            bridge_transport.send_text(left, '{"ok":true}')
            self.assertEqual(bridge_transport.recv_text(right), '{"ok":true}')
            bridge_transport.send_text(right, '{"server":true}')
            self.assertEqual(bridge_transport.recv_text(left), '{"server":true}')
        finally:
            left.close()
            right.close()

    def test_safe_bridge_client_can_use_main_thread_executor_runner(self):
        executor = DirectExecutor()
        calls = []
        client = bridge_transport.SafeBridgeClient(
            executor=executor,
            registry_provider=lambda: {},
            executor_runner=lambda tool, args: calls.append((tool, args)) or {"ok": True, "result": {"thread": "main"}},
        )
        left, right = socket.socketpair()
        try:
            bridge_transport.send_text(
                left,
                '{"id":"call-1","method":"tool.call","params":{"tool":"anki_note_create","arguments":{"deck":"Default","model":"Basic"}}}',
            )
            client._handle_message(right, bridge_transport.recv_text(right))
            response = json.loads(bridge_transport.recv_text(left))
        finally:
            left.close()
            right.close()

        self.assertEqual(calls, [("anki_note_create", {"deck": "Default", "model": "Basic"})])
        self.assertEqual(response["params"]["result"]["thread"], "main")
        self.assertTrue(response["params"]["ok"])
        self.assertIn("durationMs", response["params"])

    def test_bridge_hello_payload_advertises_versioned_anki_tools(self):
        payload = bridge_transport.bridge_hello_payload(
            {
                "bridgeId": "anki-local-profile",
                "addonVersion": "0.1.0",
                "profileHash": "profile-1",
                "collectionHash": "collection-1",
                "capabilities": {"paths": ["safe_bridge"]},
                "tools": [
                    {"name": "anki_app_get_state", "risk": "read"},
                    {"name": "anki_run_python", "risk": "dev_exec"},
                    {"name": "other.sidebar.show_status", "risk": "ui"},
                    {"name": "other.exec.run", "risk": "system_exec"},
                ],
            },
            {"DECKHAND_ANKI_BRIDGE_TOKEN": "pairing-token"},
        )

        self.assertEqual(payload["method"], "anki_bridge_hello")
        self.assertEqual(payload["params"]["protocolVersion"], "deckhand.ankiBridge.v1")
        self.assertNotIn("protocol", payload["params"])
        self.assertEqual(payload["params"]["pairingToken"], "pairing-token")
        self.assertEqual(len(payload["params"]["tools"]), 2)
        self.assertEqual(payload["params"]["tools"][0]["name"], "anki_app_get_state")
        self.assertEqual(payload["params"]["tools"][1]["name"], "anki_run_python")
        self.assertEqual(payload["params"]["capabilities"]["paths"], ["safe_bridge"])
        self.assertEqual(payload["params"]["profileHash"], "profile-1")
        self.assertEqual(payload["params"]["collectionHash"], "collection-1")

    def test_bridge_hello_uses_companion_token_as_pairing_fallback(self):
        payload = bridge_transport.bridge_hello_payload(
            {"tools": [{"name": "anki_app_get_state"}]},
            {"DECKHAND_COMPANION_TOKEN": "companion-token"},
        )

        self.assertEqual(payload["params"]["pairingToken"], "companion-token")

    def test_bridge_url_includes_companion_token_query(self):
        self.assertEqual(
            bridge_transport.with_companion_token("ws://127.0.0.1:28765/ws/anki", "token-1"),
            "ws://127.0.0.1:28765/ws/anki?token=token-1",
        )
        self.assertEqual(
            bridge_transport.with_companion_token("ws://127.0.0.1:28765/ws/anki?mode=test", "token-1"),
            "ws://127.0.0.1:28765/ws/anki?mode=test&token=token-1",
        )

    def test_safe_bridge_defaults_to_desktop_sidecar(self):
        self.assertEqual(bridge_transport.DEFAULT_URL, "ws://127.0.0.1:28765/ws/anki")
        client = bridge_transport.SafeBridgeClient(
            executor=DirectExecutor(),
            registry_provider=lambda: {},
        )
        self.assertEqual(client._url, "ws://127.0.0.1:28765/ws/anki")

    def test_safe_bridge_url_env_override_is_still_supported(self):
        original = os.environ.get("DECKHAND_SAFE_BRIDGE_URL")
        os.environ["DECKHAND_SAFE_BRIDGE_URL"] = "ws://127.0.0.1:19999/ws/anki-test"
        try:
            client = bridge_transport.SafeBridgeClient(
                executor=DirectExecutor(),
                registry_provider=lambda: {},
            )
        finally:
            if original is None:
                os.environ.pop("DECKHAND_SAFE_BRIDGE_URL", None)
            else:
                os.environ["DECKHAND_SAFE_BRIDGE_URL"] = original

        self.assertEqual(client._url, "ws://127.0.0.1:19999/ws/anki-test")

    def test_safe_bridge_hello_replaces_legacy_register_payload(self):
        source = (ADDON / "deckhand" / "bridge_transport.py").read_text(encoding="utf-8")

        self.assertIn("send_text(sock, json.dumps(bridge_hello_payload(self._registry_provider())))", source)
        self.assertIn('"anki_bridge_hello"', source)
        self.assertNotIn('"anki_bridge_register"', source)

    def test_safe_bridge_start_uses_retry_loop(self):
        source = (ADDON / "deckhand" / "bridge_transport.py").read_text(encoding="utf-8")

        self.assertIn("target=self._run_forever", source)
        self.assertIn("DECKHAND_SAFE_BRIDGE_RETRY_SECONDS", source)

    def test_safe_bridge_restarts_companion_after_newer_addon_takeover(self):
        source = (ADDON / "deckhand" / "bridge_transport.py").read_text(encoding="utf-8")

        self.assertIn('if error == "companion_takeover_newer_addon":', source)
        self.assertIn("def _restart_companion_after_takeover(self) -> None:", source)
        self.assertIn("DECKHAND_COMPANION_TAKEOVER_RESTART_DELAY_SECONDS", source)
        self.assertIn("companion.ensure_running(logger=self._logger, force=True)", source)

    def test_bridge_transport_idle_timeout_returns_no_message(self):
        left, right = socket.socketpair()
        try:
            left.settimeout(0.01)
            self.assertIsNone(bridge_transport.recv_text(left))
        finally:
            left.close()
            right.close()

    def test_catalog_uses_standard_mcp_risk_categories_without_approvals(self):
        for entry in command_catalog():
            self.assertFalse(hasattr(entry, "approval"), entry.name)
            self.assertNotIn("approved", entry.input_schema.properties, entry.name)
            self.assertTrue(entry.paths, entry.name)
            self.assertTrue(entry.description, entry.name)

        entries = {entry.name: entry for entry in command_catalog()}
        backup_description = entries["anki_backup_create"].description
        run_python_description = entries["anki_run_python"].description
        run_python_schema = entries["anki_run_python"].input_schema
        runtime_description = entries["anki_runtime_info"].description
        self.assertIn("does not include media files", backup_description)
        self.assertIn("Use before major collection operations", backup_description)
        self.assertIn("bulk edits, deletes, imports, template changes, or scheduling changes", backup_description)
        self.assertIn("created:false", backup_description)
        self.assertIn("includeMedia:true", backup_description)
        self.assertIn("Prefer Anki APIs via mw/aqt", run_python_description)
        self.assertIn("do not edit the collection SQLite database or media folder directly", run_python_description)
        self.assertIn("main Qt thread", run_python_description)
        self.assertIn("deckhand.web", run_python_description)
        self.assertIn("inspect.getdoc(web)", run_python_description)
        self.assertIn("web.status()", run_python_description)
        self.assertIn("web.pages()", run_python_description)
        self.assertIn('web.page(preferred="main")', run_python_description)
        self.assertIn("p.snapshot(max_elements=200, max_tree_nodes=250, file=None)", run_python_description)
        self.assertIn('p.screenshot(file, format="png")', run_python_description)
        self.assertIn("p.eval(script)", run_python_description)
        self.assertIn("p.click(uid=..., selector=..., text=..., x=..., y=...)", run_python_description)
        self.assertIn("p.type(text, uid=..., selector=..., clear=False)", run_python_description)
        self.assertIn('result = web.page().screenshot("/tmp/anki-card.png")', run_python_description)
        self.assertIn('result = web.page().html(file="/tmp/anki.html")', run_python_description)
        self.assertIn('result = web.page().eval("document.body.innerText")', run_python_description)
        self.assertIn("resultFilePath/resultFormat", run_python_description)
        self.assertIn("code", run_python_schema.properties)
        self.assertNotIn("snippet", run_python_schema.properties)
        self.assertEqual(run_python_schema.required, ["code"])
        self.assertNotIn("safe anki_run_python snippets", runtime_description)

    def test_catalog_tracks_current_implemented_tools(self):
        implemented = {
            entry.name for entry in command_catalog() if entry.status == "implemented"
        }

        self.assertIn("anki_app_get_state", implemented)
        self.assertNotIn("anki_context_get_current", implemented)
        self.assertIn("anki_note_search", implemented)
        self.assertIn("anki_note_get", implemented)
        self.assertIn("anki_note_update_fields", implemented)
        self.assertIn("anki_note_add_tag", implemented)
        self.assertIn("anki_runtime_info", implemented)
        self.assertNotIn("anki_context_get_deck_browser", implemented)
        self.assertNotIn("anki_media_add_url", implemented)
        self.assertNotIn("anki_browser_search", implemented)
        self.assertNotIn("anki_browser_apply_tags", implemented)
        self.assertNotIn("anki_editor_get_focused_note", implemented)
        self.assertNotIn("anki_editor_set_field", implemented)
        self.assertNotIn("anki_editor_insert_media", implemented)
        self.assertIn("anki_export_notes", implemented)
        self.assertIn("anki_export_deck_snapshot", implemented)
        self.assertIn("anki_export_deck_package", implemented)
        self.assertIn("anki_export_collection_package", implemented)
        self.assertIn("anki_backup_create", implemented)
        entries = {entry.name: entry for entry in command_catalog()}
        self.assertNotIn(
            "legacy",
            entries["anki_export_collection_package"].input_schema.properties,
        )
        removed = {
            "anki_note_create_draft",
            "anki_note_update_fields_draft",
            "anki_note_duplicate",
            "anki_note_bulk_add_tag",
            "anki_context_get_selection",
            "anki_context_get_deck_browser",
            "anki_navigate_deck_browser",
            "anki_navigate_browser_search",
            "anki_navigate_note",
            "anki_navigate_card",
            "anki_card_reposition",
            "anki_deck_rename",
            "anki_model_get_fields",
            "anki_model_get_templates",
            "anki_model_get_css",
            "anki_template_render",
            "anki_template_diff",
            "anki_template_validate",
            "anki_template_update_draft",
            "anki_media_find_refs",
            "anki_media_validate_missing",
            "anki_media_validate_unused",
            "anki_media_attachments",
            "anki_media_add_url",
            "anki_browser_search",
            "anki_browser_apply_tags",
            "anki_editor_get_focused_note",
            "anki_editor_set_field",
            "anki_editor_insert_media",
            "anki_browser_set_search",
            "anki_browser_get_selection",
            "anki_editor_preview_current_note",
            "anki_editor_get_fields",
            "anki_import_preview_csv",
            "anki_import_apply_csv",
            "anki_backup_collection",
            "anki_dev_sql_read",
            "anki_dev_list_hooks",
            "anki_dev_get_addon_config",
            "anki_dev_diagnostics",
            "anki_webengine_list_console_messages",
            "anki_webengine_list_network_requests",
            "anki_webengine_status",
            "anki_webengine_list_pages",
            "anki_webengine_take_snapshot",
            "anki_webengine_take_screenshot",
            "anki_webengine_evaluate_script",
            "anki_webengine_click",
            "anki_webengine_type_text",
            "anki_webengine_press_key",
            "anki_webengine_wait_for",
            "anki_webengine_send_cdp_command",
        }
        self.assertTrue(removed.isdisjoint(implemented))

    def test_webengine_tools_are_not_public_mcp_catalog_entries(self):
        entries = {entry.name: entry for entry in command_catalog()}

        self.assertIn("anki_run_python", entries)
        self.assertFalse(any(name.startswith("anki_webengine_") for name in entries))

    def test_direct_executor_dispatches_registered_tool(self):
        executor = DirectExecutor()
        executor.register("anki_run_python", lambda args: {"args": args})

        result = executor.call("anki_run_python", {"ok": True})

        self.assertTrue(result.ok)
        self.assertEqual(result.result, {"args": {"ok": True}})

    def test_run_python_executor_reads_code_without_snippet_alias(self):
        with mock.patch.object(runtime_snippets, "run_python_snippet", return_value={"result": 3}) as run:
            result = addon_shell._run_python_snippet(
                {
                    "code": "result = 3",
                    "resultFilePath": "/tmp/deckhand-result.json",
                    "resultFormat": "json",
                    "inlineLimitBytes": 42,
                }
            )

        self.assertEqual(result, {"result": 3})
        run.assert_called_once_with(
            "result = 3",
            result_file_path="/tmp/deckhand-result.json",
            result_format="json",
            inline_limit_bytes=42,
        )

        with mock.patch.object(runtime_snippets, "run_python_snippet", return_value={"result": None}) as run:
            addon_shell._run_python_snippet({"snippet": "result = 9"})

        run.assert_called_once_with(
            "",
            result_file_path=None,
            result_format="json",
            inline_limit_bytes=runtime_snippets.DEFAULT_INLINE_LIMIT_BYTES,
        )

    def test_direct_executor_reports_missing_tool(self):
        executor = DirectExecutor()

        result = executor.call("anki_missing")

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "tool_not_found")

    def test_direct_executor_can_unregister_tools(self):
        executor = DirectExecutor()
        executor.register("anki_run_python", lambda _args: {"ok": True})

        executor.unregister("anki_run_python")

        self.assertEqual(executor.tools(), [])

    def test_direct_executor_rejects_non_anki_namespace(self):
        executor = DirectExecutor()

        with self.assertRaises(ValueError):
            executor.register("other.files.read", lambda args: {"args": args})

    def test_webengine_send_cdp_command_executes_without_deckhand_approval_envelope(self):
        target = webengine_tools.TargetResolution(
            "ws://127.0.0.1:9222/devtools/page/page-1",
            {
                "id": "page-1",
                "type": "page",
                "title": "main webview",
                "url": "u",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/page-1",
                "selectionReason": "preferred_main_webview",
            },
        )
        with mock.patch.object(
            webengine_tools,
            "_resolve_target",
            return_value=target,
        ), mock.patch.object(
            webengine_tools,
            "_cdp_request",
            return_value={"id": 1, "result": {"result": {"value": "ok"}}},
        ):
            result = webengine_tools.send_cdp_command({"method": "Runtime.evaluate"})

        self.assertNotIn("requiresApproval", result)
        self.assertNotIn("ok", result)
        self.assertNotIn("durationMs", result)
        self.assertEqual(result["method"], "Runtime.evaluate")
        self.assertEqual(result["target"]["selectionReason"], "preferred_main_webview")
        self.assertEqual(result["response"]["result"]["result"]["value"], "ok")

    def test_webengine_snapshot_uses_runtime_evaluate(self):
        calls = []

        def fake_request(_url, method, params, timeout):
            calls.append((method, params, timeout))
            return {
                "id": 1,
                "result": {
                    "result": {
                        "value": {
                            "snapshotId": "1",
                            "title": "T",
                            "url": "u",
                            "text": 'uid=e1_0 button "Show Answer"\n',
                            "root": {"uid": "e1_0", "role": "button", "name": "Show Answer"},
                            "elements": {"e1_0": {"uid": "e1_0", "role": "button", "name": "Show Answer", "selector": "#show"}},
                            "elementCount": 1,
                            "treeNodeCount": 1,
                            "verbose": False,
                        }
                    }
                },
            }

        target = webengine_tools.TargetResolution(
            "ws://127.0.0.1:9222/devtools/page/page-1",
            {"id": "page-1", "type": "page", "title": "main webview", "url": "u", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/page-1", "selectionReason": "preferred_main_webview"},
        )
        with mock.patch.object(webengine_tools, "_resolve_target", return_value=target), mock.patch.object(webengine_tools, "_cdp_request", side_effect=fake_request):
            result = webengine_tools.take_snapshot({"maxElements": 2, "maxTreeNodes": 3})

        self.assertEqual(calls[0][0], "Runtime.evaluate")
        self.assertIn("__deckhandSnapshot", calls[0][1]["expression"])
        self.assertIn("uidFor", calls[0][1]["expression"])
        self.assertEqual(result["snapshot"]["text"], 'uid=e1_0 button "Show Answer"\n')
        self.assertEqual(result["snapshot"]["elements"]["e1_0"]["selector"], "#show")
        self.assertEqual(result["target"]["title"], "main webview")
        self.assertNotIn("ok", result)
        self.assertNotIn("durationMs", result)

    def test_webengine_snapshot_can_write_text_artifact(self):
        target = webengine_tools.TargetResolution(
            "ws://127.0.0.1:9222/devtools/page/page-1",
            {"id": "page-1", "type": "page", "title": "main webview", "url": "u", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/page-1", "selectionReason": "preferred_main_webview"},
        )
        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "snapshot.txt"
            response = {"id": 1, "result": {"result": {"value": {"snapshotId": "7", "text": "uid=e7_0 button\n"}}}}
            with mock.patch.object(webengine_tools, "_resolve_target", return_value=target), mock.patch.object(webengine_tools, "_cdp_request", return_value=response):
                result = webengine_tools.take_snapshot({"filePath": str(output)})

            self.assertEqual(output.read_text(encoding="utf-8"), "uid=e7_0 button\n")
            self.assertEqual(result["snapshotId"], "7")
            self.assertEqual(result["path"], str(output))
            self.assertNotIn("snapshot", result)

    def test_webengine_screenshot_requires_file_path_and_writes_metadata_only(self):
        with self.assertRaises(webengine_tools.WebEngineToolError):
            webengine_tools.take_screenshot({})

        with tempfile.TemporaryDirectory() as tmpdir:
            output = Path(tmpdir) / "capture.png"
            encoded = base64.b64encode(b"png-bytes").decode("ascii")
            target = webengine_tools.TargetResolution(
                "ws://127.0.0.1:9222/devtools/page/page-1",
                {"id": "page-1", "type": "page", "title": "main webview", "url": "u", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/page-1", "selectionReason": "preferred_main_webview"},
            )
            with mock.patch.object(webengine_tools, "_resolve_target", return_value=target), mock.patch.object(webengine_tools, "_cdp_request", return_value={"id": 1, "result": {"data": encoded}}):
                result = webengine_tools.take_screenshot({"filePath": str(output)})

            self.assertEqual(output.read_bytes(), b"png-bytes")
            self.assertEqual(result["path"], str(output))
            self.assertEqual(result["bytes"], len(b"png-bytes"))
            self.assertEqual(result["target"]["id"], "page-1")
            self.assertNotIn("data", result)

    def test_webengine_evaluate_click_type_press_and_wait_send_expected_cdp(self):
        calls = []

        def fake_request(_url, method, params, timeout):
            calls.append((method, params))
            if method == "Runtime.evaluate" and "getBoundingClientRect" in params.get("expression", ""):
                return {"id": 1, "result": {"result": {"value": {"ok": True, "x": 10, "y": 20}}}}
            if method == "Runtime.evaluate" and "document.querySelector" in params.get("expression", ""):
                return {"id": 1, "result": {"result": {"value": {"ok": True}}}}
            return {"id": 1, "result": {"result": {"value": True}}}

        target = webengine_tools.TargetResolution(
            "ws://127.0.0.1:9222/devtools/page/page-1",
            {"id": "page-1", "type": "page", "title": "main webview", "url": "u", "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/page-1", "selectionReason": "preferred_main_webview"},
        )
        with mock.patch.object(webengine_tools, "_resolve_target", return_value=target), mock.patch.object(webengine_tools, "_cdp_request", side_effect=fake_request):
            webengine_tools.evaluate_script({"script": "1 + 1"})
            webengine_tools.click({"uid": "e1_0"})
            webengine_tools.type_text({"uid": "e1_1", "text": "abc", "clear": True})
            webengine_tools.press_key({"key": "Enter"})
            webengine_tools.wait_for({"expression": "window.ready === true", "timeoutSeconds": 0.1})

        methods = [method for method, _params in calls]
        self.assertIn("Runtime.evaluate", methods)
        self.assertIn("Input.dispatchMouseEvent", methods)
        self.assertIn("Input.insertText", methods)
        self.assertIn("Input.dispatchKeyEvent", methods)
        self.assertTrue(any("snapshot_uid_not_found" in params.get("expression", "") for method, params in calls if method == "Runtime.evaluate"))
        self.assertTrue(any("window.ready" in params.get("expression", "") for method, params in calls if method == "Runtime.evaluate"))

    def test_webengine_target_resolution_prefers_main_webview_and_reports_reason(self):
        pages = [
            {
                "id": "toolbar",
                "type": "page",
                "title": "top toolbar",
                "url": "http://127.0.0.1:57037/_anki/legacyPageData?id=1",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/toolbar",
            },
            {
                "id": "main",
                "type": "page",
                "title": "main webview",
                "url": "http://127.0.0.1:57037/_anki/legacyPageData?id=2",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/main",
            },
        ]

        with mock.patch.object(webengine_tools, "_list_pages", return_value=pages):
            target = webengine_tools._resolve_target({})

        self.assertEqual(target.websocket_url, "ws://127.0.0.1:9222/devtools/page/main")
        self.assertEqual(target.target["id"], "main")
        self.assertEqual(target.target["selectionReason"], "preferred_main_webview")

    def test_webengine_target_resolution_supports_first_strict_and_explicit_selectors(self):
        pages = [
            {
                "id": "toolbar",
                "type": "page",
                "title": "top toolbar",
                "url": "http://127.0.0.1:57037/_anki/legacyPageData?id=1",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/toolbar",
            },
            {
                "id": "main",
                "type": "page",
                "title": "main webview",
                "url": "http://127.0.0.1:57037/_anki/legacyPageData?id=2",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/main",
            },
        ]

        with mock.patch.object(webengine_tools, "_list_pages", return_value=pages):
            first = webengine_tools._resolve_target({"preferredTarget": "first"})
            by_id = webengine_tools._resolve_target({"pageId": "main", "preferredTarget": "first"})
            by_title = webengine_tools._resolve_target({"title": "toolbar"})
            by_url = webengine_tools._resolve_target({"urlContains": "id=2"})
            direct = webengine_tools._resolve_target({"webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/direct"})
            with self.assertRaises(webengine_tools.WebEngineToolError) as strict_error:
                webengine_tools._resolve_target({"preferredTarget": "strict"})

        self.assertEqual(first.target["id"], "toolbar")
        self.assertEqual(first.target["selectionReason"], "first_page")
        self.assertEqual(by_id.target["selectionReason"], "matched_page_id")
        self.assertEqual(by_title.target["id"], "toolbar")
        self.assertEqual(by_title.target["selectionReason"], "matched_title")
        self.assertEqual(by_url.target["id"], "main")
        self.assertEqual(by_url.target["selectionReason"], "matched_url")
        self.assertEqual(direct.target["selectionReason"], "explicit_websocket_url")
        self.assertEqual(str(strict_error.exception), "ambiguous_webengine_page")

    def test_webengine_status_and_list_pages_read_local_cdp_http(self):
        version_body = json.dumps({"Browser": "Anki/", "Protocol-Version": "1.3"}).encode()
        pages_body = json.dumps(
            [
                {
                    "id": "page-1",
                    "type": "page",
                    "title": "main webview",
                    "url": "http://127.0.0.1:57037/_anki/legacyPageData?id=1",
                    "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/page-1",
                }
            ]
        ).encode()

        server = socket.socket()
        server.bind(("127.0.0.1", 0))
        server.listen(2)
        port = server.getsockname()[1]

        def serve_once() -> None:
            connection, _address = server.accept()
            with connection:
                request = connection.recv(4096)
                body = pages_body if b"/json/list" in request else version_body
                connection.sendall(
                    b"HTTP/1.1 200 OK\r\n"
                    + f"Content-Length: {len(body)}\r\n".encode()
                    + b"Content-Type: application/json\r\n\r\n"
                    + body
                )

        import threading

        threads = [threading.Thread(target=serve_once), threading.Thread(target=serve_once)]
        for thread in threads:
            thread.start()
        try:
            status = webengine_tools.status(port=port)
            pages = webengine_tools.list_pages(port=port)
        finally:
            server.close()
            for thread in threads:
                thread.join(timeout=1)

        self.assertTrue(status["available"])
        self.assertEqual(status["version"]["Browser"], "Anki/")
        self.assertNotIn("ok", status)
        self.assertNotIn("durationMs", status)
        self.assertEqual(pages["count"], 1)
        self.assertEqual(pages["pages"][0]["title"], "main webview")
        self.assertNotIn("ok", pages)
        self.assertNotIn("durationMs", pages)

    def test_web_sdk_page_introspection_and_snapshot_wrapper(self):
        self.assertIn("Small Anki WebEngine SDK", web.__doc__)
        self.assertIn("page", [name for name in dir(web) if not name.startswith("_")])
        with mock.patch.object(webengine_tools, "take_snapshot", return_value={"snapshot": {"text": "ok"}}) as take_snapshot:
            result = web.page(title="main").snapshot(max_elements=3, max_tree_nodes=4, file="/tmp/snapshot.txt")

        self.assertEqual(result, {"snapshot": {"text": "ok"}})
        take_snapshot.assert_called_once()
        args = take_snapshot.call_args.args[0]
        self.assertEqual(args["preferredTarget"], "main")
        self.assertEqual(args["title"], "main")
        self.assertEqual(args["maxElements"], 3)
        self.assertEqual(args["maxTreeNodes"], 4)
        self.assertEqual(args["filePath"], "/tmp/snapshot.txt")

    def test_web_sdk_html_writes_file_artifact(self):
        page = web.page()
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "page.html"
            with mock.patch.object(web.Page, "eval", return_value="<html><body>ok</body></html>"):
                result = page.html(file=str(path))

            self.assertEqual(path.read_text(encoding="utf-8"), "<html><body>ok</body></html>")
            self.assertEqual(result["path"], str(path))
            self.assertEqual(result["format"], "html")

    def test_web_sdk_screenshot_click_and_type_delegate_to_webengine(self):
        page = web.page(preferred="first", port=9222)
        with mock.patch.object(webengine_tools, "take_screenshot", return_value={"path": "/tmp/a.png"}) as take_screenshot:
            self.assertEqual(page.screenshot("/tmp/a.png", format="png"), {"path": "/tmp/a.png"})
        with mock.patch.object(webengine_tools, "click", return_value={"clicked": True}) as click:
            self.assertEqual(page.click(selector="#answer"), {"clicked": True})
        with mock.patch.object(webengine_tools, "type_text", return_value={"textLength": 2}) as type_text:
            self.assertEqual(page.type("ok", selector="#answer"), {"textLength": 2})

        self.assertEqual(take_screenshot.call_args.args[0]["preferredTarget"], "first")
        self.assertEqual(take_screenshot.call_args.args[0]["filePath"], "/tmp/a.png")
        self.assertEqual(click.call_args.args[0]["selector"], "#answer")
        self.assertEqual(type_text.call_args.args[0]["text"], "ok")

    def test_web_sdk_runs_blocking_cdp_work_off_qt_main_thread(self):
        main_thread = threading.get_ident()
        event = threading.Event()

        class FakeApp:
            process_count = 0

            def processEvents(self):
                self.process_count += 1
                event.set()

        app = FakeApp()
        original = web._qt_app_state
        web._qt_app_state = lambda: (app, True)
        try:
            result = web._run_cdp(lambda: {"thread": threading.get_ident(), "unblocked": event.wait(1)})
        finally:
            web._qt_app_state = original

        self.assertNotEqual(result["thread"], main_thread)
        self.assertTrue(result["unblocked"])
        self.assertGreater(app.process_count, 0)

    def test_bridge_status_updates(self):
        status = BridgeStatus()
        status.update("connected", "test bridge")

        self.assertEqual(status.to_dict()["state"], "connected")
        self.assertEqual(status.to_dict()["detail"], "test bridge")

    def test_snippet_executes_without_deckhand_approval_envelope(self):
        original = runtime_snippets._anki_snippet_globals
        runtime_snippets._anki_snippet_globals = lambda: {
            "__builtins__": {"len": len},
            "mw": object(),
            "result": None,
        }
        try:
            result = runtime_snippets.run_python_snippet("result = len('mcp')")
        finally:
            runtime_snippets._anki_snippet_globals = original

        self.assertEqual(result["result"], 3)
        self.assertTrue(result["resultInline"])
        self.assertFalse(result["resultTruncated"])
        self.assertFalse(result["resultOmitted"])
        self.assertIsNone(result["resultPreview"])
        self.assertIsNone(result["artifact"])
        self.assertNotIn("requiresApproval", result)
        self.assertNotIn("snippetPreview", result)
        self.assertNotIn("ok", result)
        self.assertNotIn("durationMs", result)
        self.assertNotIn("approval", result)
        self.assertNotIn("approved", result)

    def test_snippet_executes_with_anki_globals(self):
        original = runtime_snippets._anki_snippet_globals
        runtime_snippets._anki_snippet_globals = lambda: {
            "__builtins__": {"len": len, "str": str},
            "mw": object(),
            "result": None,
        }
        try:
            result = runtime_snippets.run_python_snippet("result = len(str(mw))")
        finally:
            runtime_snippets._anki_snippet_globals = original

        self.assertIn("result", result)
        self.assertIsInstance(result["result"], int)

    def test_snippet_executes_normal_imports(self):
        original = runtime_snippets._anki_snippet_globals
        runtime_snippets._anki_snippet_globals = lambda: {
            "__builtins__": __import__("builtins"),
            "mw": object(),
            "result": None,
        }
        try:
            result = runtime_snippets.run_python_snippet("import math\nresult = math.ceil(1.2)")
        finally:
            runtime_snippets._anki_snippet_globals = original

        self.assertEqual(result["result"], 2)

    def test_snippet_omits_large_result_without_mutating_result_data(self):
        original = runtime_snippets._anki_snippet_globals
        runtime_snippets._anki_snippet_globals = lambda: {
            "__builtins__": {"len": len},
            "mw": object(),
            "result": None,
        }
        try:
            result = runtime_snippets.run_python_snippet("result = 'x' * 20", inline_limit_bytes=10)
        finally:
            runtime_snippets._anki_snippet_globals = original

        self.assertIsNone(result["result"])
        self.assertFalse(result["resultInline"])
        self.assertFalse(result["resultTruncated"])
        self.assertTrue(result["resultOmitted"])
        self.assertIn('"xxxxxxxxxxxxxxxxxxxx"', result["resultPreview"])
        self.assertIn("rerun with resultFilePath", result["message"])

    def test_snippet_writes_large_result_artifact(self):
        original = runtime_snippets._anki_snippet_globals
        runtime_snippets._anki_snippet_globals = lambda: {
            "__builtins__": {"len": len},
            "mw": object(),
            "result": None,
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "result.json"
            try:
                result = runtime_snippets.run_python_snippet(
                    "result = {'text': 'x' * 20}",
                    result_file_path=str(path),
                    inline_limit_bytes=1,
                )
            finally:
                runtime_snippets._anki_snippet_globals = original

            self.assertIsNone(result["result"])
            self.assertFalse(result["resultInline"])
            self.assertFalse(result["resultOmitted"])
            self.assertEqual(result["artifact"]["path"], str(path))
            self.assertEqual(result["artifact"]["format"], "json")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"text": "xxxxxxxxxxxxxxxxxxxx"})

    def test_snippet_text_result_format_artifact(self):
        original = runtime_snippets._anki_snippet_globals
        runtime_snippets._anki_snippet_globals = lambda: {
            "__builtins__": {},
            "mw": object(),
            "result": None,
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "result.txt"
            try:
                result = runtime_snippets.run_python_snippet(
                    "result = 'plain text'",
                    result_file_path=str(path),
                    result_format="text",
                )
            finally:
                runtime_snippets._anki_snippet_globals = original

            self.assertEqual(path.read_text(encoding="utf-8"), "plain text")
            self.assertEqual(result["artifact"]["format"], "text")

    def test_snippet_unserializable_result_falls_back_to_repr(self):
        original = runtime_snippets._anki_snippet_globals
        runtime_snippets._anki_snippet_globals = lambda: {
            "__builtins__": {"object": object},
            "mw": object(),
            "result": None,
        }
        try:
            result = runtime_snippets.run_python_snippet("result = object()")
        finally:
            runtime_snippets._anki_snippet_globals = original

        self.assertIn("object object", result["result"])

    def test_snippet_blocks_builtin_exit(self):
        original = runtime_snippets._anki_snippet_globals
        runtime_snippets._anki_snippet_globals = lambda: {
            "__builtins__": {"exit": runtime_snippets._blocked_callable("exit")},
            "mw": object(),
            "result": None,
        }
        try:
            with self.assertRaises(runtime_snippets.DevToolError) as context:
                runtime_snippets.run_python_snippet("exit()")
        finally:
            runtime_snippets._anki_snippet_globals = original

        self.assertEqual(str(context.exception), "snippet_forbidden_operation:exit")

    def test_snippet_blocks_sys_exit(self):
        original = runtime_snippets._anki_snippet_globals
        runtime_snippets._anki_snippet_globals = lambda: {
            "__builtins__": {
                "__import__": runtime_snippets._guarded_import(
                    {"sys": runtime_snippets.GuardedModuleProxy(__import__("sys"), {"exit": "sys.exit"})}
                )
            },
            "mw": object(),
            "result": None,
        }
        try:
            with self.assertRaises(runtime_snippets.DevToolError) as context:
                runtime_snippets.run_python_snippet("import sys\nsys.exit(2)")
        finally:
            runtime_snippets._anki_snippet_globals = original

        self.assertEqual(str(context.exception), "snippet_forbidden_operation:sys.exit")

    def test_snippet_guarded_modules_omits_posix_when_unavailable(self):
        original = runtime_snippets.posix_module
        runtime_snippets.posix_module = None
        try:
            guarded_modules = runtime_snippets._guarded_module_map(object(), object())
        finally:
            runtime_snippets.posix_module = original

        self.assertNotIn("posix", guarded_modules)
        self.assertIn("sys", guarded_modules)
        self.assertIn("os", guarded_modules)

    def test_snippet_guarded_modules_blocks_posix_exit_when_available(self):
        fake_posix = SimpleNamespace(_exit=lambda _code=0: None)
        original = runtime_snippets.posix_module
        runtime_snippets.posix_module = fake_posix
        try:
            guarded_modules = runtime_snippets._guarded_module_map(object(), object())
        finally:
            runtime_snippets.posix_module = original

        with self.assertRaises(runtime_snippets.DevToolError) as context:
            guarded_modules["posix"]._exit(0)

        self.assertEqual(str(context.exception), "snippet_forbidden_operation:posix._exit")

    def test_snippet_normalizes_system_exit(self):
        original = runtime_snippets._anki_snippet_globals
        runtime_snippets._anki_snippet_globals = lambda: {
            "__builtins__": __import__("builtins"),
            "mw": object(),
            "result": None,
        }
        try:
            with self.assertRaises(runtime_snippets.DevToolError) as context:
                runtime_snippets.run_python_snippet("raise SystemExit(3)")
        finally:
            runtime_snippets._anki_snippet_globals = original

        self.assertEqual(str(context.exception), "snippet_failed:SystemExit: 3")

    def test_typed_note_search_get_update_and_tag_shape(self):
        store = typed_tools.FakeNoteStore(
            [
                typed_tools.NoteRecord(
                    id=42,
                    fields={"Front": "capital of France", "Back": "Paris"},
                    tags=["geo"],
                )
            ]
        )

        search = typed_tools.note_search(store, "france")
        note = typed_tools.note_get(store, 42)
        updated = typed_tools.note_update_fields(store, 42, {"Back": "Paris, France"})
        tagged = typed_tools.note_add_tag(store, 42, "reviewed")

        self.assertEqual(search["noteIds"], [42])
        self.assertEqual(note["fields"]["Back"], "Paris")
        self.assertEqual(updated["updatedFields"], ["Back"])
        self.assertIn("reviewed", tagged["note"]["tags"])

    def test_real_collection_store_search_get_update_and_tag(self):
        mw = FakeMw()
        store = typed_tools.AnkiCollectionNoteStore(mw)

        search = typed_tools.note_search(store, "deckhand")
        note = typed_tools.note_get(store, 1001)
        updated = typed_tools.note_update_fields(store, 1001, {"Back": "Updated"})
        tagged = typed_tools.note_add_tag(store, 1001, "saved")

        self.assertEqual(search["noteIds"], [1001])
        self.assertEqual(note["deck"], "Default")
        self.assertEqual(note["model"], "Basic")
        self.assertNotIn("requiresApproval", updated)
        self.assertEqual(updated["note"]["fields"]["Back"], "Updated")
        self.assertTrue(mw.reset_called)
        self.assertIn("saved", tagged["note"]["tags"])

    def test_note_authoring_create_tag_delete_shapes(self):
        store = typed_tools.FakeNoteStore(
            [
                typed_tools.NoteRecord(
                    id=42,
                    fields={"Front": "capital of France", "Back": "Paris"},
                    tags=["geo"],
                )
            ]
        )

        created = typed_tools.note_create(
            store,
            "Default",
            "Basic",
            {"Front": "new", "Back": "note"},
            ["draft"],
        )
        removed = typed_tools.note_remove_tag(store, created["note"]["id"], "draft")
        set_tags = typed_tools.note_set_tags(store, created["note"]["id"], ["final"])
        deleted = typed_tools.note_delete(store, [created["note"]["id"]])

        self.assertNotIn("draft", removed["note"]["tags"])
        self.assertEqual(set_tags["tags"], ["final"])
        self.assertEqual(deleted["deletedNoteIds"], [created["note"]["id"]])

    def test_real_collection_store_authoring_paths(self):
        mw = FakeMw()
        store = typed_tools.AnkiCollectionNoteStore(mw)

        created = typed_tools.note_create(
            store,
            "Default",
            "Basic",
            {"Front": "created", "Back": "body"},
            ["created"],
        )
        created_id = created["note"]["id"]
        removed = typed_tools.note_remove_tag(store, created_id, "created")
        tagged = typed_tools.note_set_tags(store, created_id, ["kept"])
        deleted = typed_tools.note_delete(store, [created_id])

        self.assertEqual(created["note"]["fields"]["Front"], "created")
        self.assertNotIn("created", removed["note"]["tags"])
        self.assertEqual(tagged["note"]["tags"], ["kept"])
        self.assertEqual(deleted["count"], 1)

    def test_direct_executor_catches_base_exception(self):
        executor = DirectExecutor()
        executor.register("anki_context_raise_exit", lambda _args: (_ for _ in ()).throw(SystemExit(9)))

        result = executor.call("anki_context_raise_exit", {})

        self.assertFalse(result.ok)
        self.assertEqual(result.error, "execution_failed: 9")

    def test_direct_executor_returns_standard_tool_results(self):
        executor = DirectExecutor()
        executor.register("anki_context_get_profile", lambda _args: {"name": "Test User"})
        executor.register(
            "anki_note_update_fields",
            lambda _args: {"note": {"id": 1}, "updatedFields": ["Back"]},
        )

        profile = executor.call("anki_context_get_profile", {})
        mutation = executor.call("anki_note_update_fields", {"noteId": 1})

        self.assertTrue(profile.ok)
        self.assertEqual(profile.result["name"], "Test User")
        self.assertTrue(mutation.ok)
        self.assertNotIn("requiresApproval", mutation.result)
        self.assertEqual(mutation.result["updatedFields"], ["Back"])

    def test_context_tools_read_fake_anki_state(self):
        mw = FakeMw()
        mw.state = "reviewer"
        mw.pm = SimpleNamespace(name="User 1", base="/tmp/anki")
        mw.reviewer = SimpleNamespace(
            card=SimpleNamespace(id=2001, nid=1001, did=1, queue=2, due=7)
        )
        mw.browser = FakeBrowser()

        context = context_tools.current_context(mw)
        selection = context_tools.current_selection(mw)
        profile = context_tools.current_profile(mw)

        self.assertEqual(context["screen"], "reviewer")
        self.assertEqual(context["reviewer"]["cardId"], 2001)
        self.assertEqual(selection["noteIds"], [1001])
        self.assertEqual(profile["name"], "User 1")

    def test_card_tools_read_preview_and_scheduler_actions(self):
        mw = FakeMw()

        card = card_tools.card_get(mw, 2001)
        by_note = card_tools.card_find_by_note(mw, 1001)
        preview = card_tools.card_preview(mw, 2001)
        suspended = card_tools.card_suspend(mw, [2001])
        unsuspended = card_tools.card_unsuspend(mw, [2001])
        buried = card_tools.card_bury(mw, [2001])
        due = card_tools.card_set_due(mw, [2001], 3)

        self.assertEqual(card["noteId"], 1001)
        self.assertEqual(by_note["cardIds"], [2001])
        self.assertIn("Deckhand", preview["front"])
        self.assertNotIn("requiresApproval", suspended)
        self.assertTrue(suspended["after"][0]["suspended"])
        self.assertFalse(unsuspended["after"][0]["suspended"])
        self.assertEqual(buried["cardIds"], [2001])
        self.assertEqual(due["days"], 3)

    def test_structure_tools_read_decks_models_and_stats(self):
        mw = FakeMw()

        decks = structure_tools.deck_list(mw)
        stats = structure_tools.deck_get_stats(mw)
        models = structure_tools.model_list(mw)
        model = structure_tools.model_get(mw, "Basic", None)
        created_deck = structure_tools.deck_create(mw, "New Deck")

        self.assertEqual(decks["count"], 1)
        self.assertEqual(stats["deck"]["name"], "Default")
        self.assertEqual(models["models"][0]["name"], "Basic")
        self.assertEqual(model["fields"], ["Front", "Back"])
        self.assertEqual(created_deck["deck"]["name"], "New Deck")
        self.assertEqual(stats["counts"], {"new": 1, "learn": 2, "review": 3})

    def test_structure_stats_match_requested_deck_due_tree_node(self):
        mw = FakeMw()
        mw.col.decks.name = lambda did: "Default" if int(did) == 1 else "Other"
        stats = structure_tools.deck_get_stats(mw, 2)

        self.assertEqual(stats["deck"]["id"], 2)
        self.assertEqual(stats["counts"], {"new": 4, "learn": 5, "review": 6})

    def test_structure_stats_report_unavailable_without_scheduler_counts(self):
        mw = FakeMw()
        mw.col.sched = SimpleNamespace()
        stats = structure_tools.deck_get_stats(mw, 1)

        self.assertIsNone(stats["counts"])
        self.assertTrue(stats["countsUnavailable"])

    def test_media_tools_sanitize_record_attach_and_get(self):
        source = TEST_TMP / "media" / "unsafe sample!.txt"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("media smoke", encoding="utf-8")
        mw = FakeMw()
        store = media_tools.AttachmentStore()

        self.assertEqual(media_tools.sanitize_filename("../unsafe sample!.txt"), "unsafe_sample_.txt")
        added = media_tools.add_file(
            mw,
            store,
            str(source),
            source_kind="packet_evidence",
            provenance={"packet": "022"},
        )
        metadata = media_tools.get(mw, added["filename"])
        attached = media_tools.attach_to_field(mw, 1001, "Back", added["filename"])

        self.assertEqual(added["attachment"]["source_kind"], "packet_evidence")
        self.assertTrue(metadata["exists"])
        self.assertNotIn("requiresApproval", attached)
        self.assertIn(added["filename"], attached["markup"])

    def test_note_mutations_prefer_collection_update_note(self):
        mw = FakeMw()
        store = typed_tools.AnkiCollectionNoteStore(mw)

        updated = store.update_fields(1001, {"Back": "Saved through update_note"})
        tagged = store.add_tag(1001, "modern-save")

        self.assertEqual(updated.fields["Back"], "Saved through update_note")
        self.assertIn("modern-save", tagged.tags)
        self.assertEqual(mw.col.updated_note_ids, [1001, 1001])
        self.assertFalse(mw.col.notes[1001].flushed)

    def test_media_field_mutations_prefer_collection_update_note(self):
        source = TEST_TMP / "media" / "update path.txt"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("media smoke", encoding="utf-8")
        mw = FakeMw()
        store = media_tools.AttachmentStore()
        added = media_tools.add_file(mw, store, str(source))

        media_tools.attach_to_field(mw, 1001, "Back", added["filename"])

        self.assertGreaterEqual(mw.col.updated_note_ids.count(1001), 1)
        self.assertFalse(mw.col.notes[1001].flushed)

    def test_note_save_falls_back_to_flush_for_older_collection(self):
        mw = FakeMw()
        mw.col.update_note = None
        note = mw.col.get_note(1001)

        typed_tools.save_note(mw, note)

        self.assertTrue(note.flushed)

    def test_import_export_backup_tools_with_fakes(self):
        mw = FakeMw()
        artifact_dir = TEST_TMP / "import-export"
        artifact_dir.mkdir(parents=True, exist_ok=True)
        exported = import_export_tools.export_notes(
            mw,
            query="deckhand",
            filePath=str(artifact_dir / "notes.json"),
            format="json",
            overwrite=True,
        )
        exported_csv = import_export_tools.export_notes(
            mw,
            query="deckhand",
            filePath=str(artifact_dir / "notes-native.csv"),
            format="csv",
            overwrite=True,
        )
        snapshot = import_export_tools.deck_snapshot(
            mw, filePath=str(artifact_dir / "snapshot.json"), overwrite=True
        )
        backup = import_export_tools.backup_create(
            mw, folderPath=str(artifact_dir / "backups")
        )
        deck_package = import_export_tools.export_deck_package(
            mw,
            filePath=str(artifact_dir / "deck.apkg"),
            deck="Default",
            includeMedia=False,
            includeScheduling=False,
            overwrite=True,
        )
        collection_package = import_export_tools.export_collection_package(
            mw,
            filePath=str(artifact_dir / "collection.colpkg"),
            includeMedia=False,
            overwrite=True,
        )

        self.assertTrue(Path(exported["artifact"]["path"]).exists())
        self.assertNotIn("notes", exported)
        self.assertTrue(Path(exported_csv["artifact"]["path"]).exists())
        self.assertEqual(exported_csv["artifact"]["kind"], "anki_note_export")
        self.assertGreaterEqual(snapshot["summary"]["deckCount"], 1)
        self.assertNotIn("decks", snapshot)
        self.assertTrue(Path(backup["backupPath"]).exists())
        self.assertFalse(backup["mediaIncluded"])
        self.assertTrue(Path(deck_package["artifact"]["path"]).exists())
        self.assertTrue(Path(collection_package["artifact"]["path"]).exists())
        self.assertNotIn("legacy", collection_package)
        self.assertEqual(mw.col._backend.collection_package_calls[0]["legacy"], False)
        self.assertEqual(mw.col._backend.backup_calls[0]["force"], True)

        with self.assertRaises(ValueError):
            import_export_tools.export_notes(mw, query="x", format="json")
        with self.assertRaises(FileExistsError):
            import_export_tools.deck_snapshot(
                mw, filePath=str(artifact_dir / "snapshot.json")
            )

if __name__ == "__main__":
    unittest.main()


class FakeNote:
    def __init__(self, note_id=1001, fields=None, tags=None) -> None:
        self.id = note_id
        self.fields = fields or {"Front": "Deckhand disposable", "Back": "Original"}
        self.tags = tags or ["deckhand"]
        self.flushed = False

    def keys(self):
        return list(self.fields)

    def __getitem__(self, key):
        return self.fields[key]

    def __setitem__(self, key, value):
        self.fields[key] = value

    def card_ids(self):
        return [self.id + 1000]

    def note_type(self):
        return {"name": "Basic"}

    def add_tag(self, tag):
        if tag not in self.tags:
            self.tags.append(tag)

    def flush(self):
        self.flushed = True


class FakeCollection:
    def __init__(self) -> None:
        self.notes = {1001: FakeNote()}
        self.cards = {2001: FakeCard()}
        self.next_note_id = 1002
        self.decks = SimpleNamespace(
            id=lambda _name: 1,
            get=lambda deck_id: {"id": deck_id, "name": "Default"},
            name=lambda _did: "Default",
            rename=lambda deck, name: deck.update({"name": name}),
            selected=lambda: 1,
            all=lambda: [{"id": 1, "name": "Default"}],
            all_names_and_ids=lambda: [SimpleNamespace(id=1, name="Default")],
        )
        self.basic_model = {
            "id": 1,
            "name": "Basic",
            "flds": [{"name": "Front"}, {"name": "Back"}],
            "tmpls": [{"name": "Card 1", "qfmt": "{{Front}}", "afmt": "{{FrontSide}}<hr>{{Back}}"}],
            "css": ".card { font-family: arial; }",
        }
        self.models = SimpleNamespace(
            by_name=lambda name: self.basic_model if name == "Basic" else None,
            get=lambda model_id: self.basic_model if model_id == 1 else None,
            all=lambda: [self.basic_model],
        )
        self.sched = FakeSched(self)
        self.db = SimpleNamespace(scalar=lambda _query, _deck_id: len(self.cards))
        self.media = FakeMedia()
        self._backend = FakeBackend()
        self.updated_note_ids = []
        self.path = str(TEST_TMP / "fake-collection.anki2")
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        Path(self.path).write_text("fake collection", encoding="utf-8")

    def find_notes(self, query):
        needle = query.lower().strip('"')
        return [
            note_id
            for note_id, note in self.notes.items()
            if needle in " ".join([*note.fields.values(), *note.tags]).lower()
        ]

    def get_note(self, note_id):
        if note_id not in self.notes:
            raise KeyError(note_id)
        return self.notes[note_id]

    def get_card(self, card_id):
        if card_id not in self.cards:
            note_id = card_id - 1000
            self.cards[card_id] = FakeCard(card_id=card_id, note_id=note_id)
        return self.cards[card_id]

    def new_note(self, _note_type):
        return FakeNote(self.next_note_id, fields={"Front": "", "Back": ""}, tags=[])

    def add_note(self, note, _deck_id):
        self.notes[note.id] = note
        self.cards[note.id + 1000] = FakeCard(card_id=note.id + 1000, note_id=note.id)
        self.next_note_id = max(self.next_note_id, note.id + 1)

    def remove_notes(self, note_ids):
        for note_id in note_ids:
            self.notes.pop(note_id, None)
            self.cards.pop(note_id + 1000, None)

    def update_note(self, note):
        self.updated_note_ids.append(int(note.id))
        self.notes[int(note.id)] = note


class FakeBackend:
    def __init__(self) -> None:
        self.backup_calls = []
        self.note_csv_calls = []
        self.anki_package_calls = []
        self.collection_package_calls = []

    def create_backup(self, *, backup_folder, force, wait_for_completion):
        self.backup_calls.append(
            {
                "backup_folder": backup_folder,
                "force": force,
                "wait_for_completion": wait_for_completion,
            }
        )
        target = Path(backup_folder) / "backup-test.anki2"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"native backup")
        return True

    def await_backup_completion(self):
        return None

    def export_note_csv(self, **kwargs):
        self.note_csv_calls.append(kwargs)
        Path(kwargs["out_path"]).write_text("Front,Back\nnative,csv\n", encoding="utf-8")
        return 1

    def export_anki_package(self, **kwargs):
        self.anki_package_calls.append(kwargs)
        Path(kwargs["out_path"]).write_bytes(b"apkg")
        return 1

    def export_collection_package(self, **kwargs):
        self.collection_package_calls.append(kwargs)
        Path(kwargs["out_path"]).write_bytes(b"colpkg")
        return None


class FakeMw:
    def __init__(self) -> None:
        self.col = FakeCollection()
        self.reset_called = False
        self.moved_to = None

    def reset(self):
        self.reset_called = True

    def moveToState(self, state):
        self.moved_to = state


class FakeMedia:
    def __init__(self) -> None:
        self.root = TEST_TMP / "fake-media"
        self.root.mkdir(parents=True, exist_ok=True)

    def add_file(self, path):
        source = Path(path)
        target = self.root / media_tools.sanitize_filename(source.name)
        target.write_bytes(source.read_bytes())
        return target.name

    def dir(self):
        return str(self.root)

    def check(self):
        return [], []


class FakeCard:
    def __init__(self, card_id=2001, note_id=1001) -> None:
        self.id = card_id
        self.nid = note_id
        self.did = 1
        self.queue = 0
        self.type = 0
        self.due = 1
        self.ivl = 0
        self.factor = 2500

    def question(self):
        return "Deckhand disposable front"

    def answer(self):
        return "Deckhand disposable back"


class FakeSched:
    version = 3

    def __init__(self, collection) -> None:
        self.collection = collection

    def suspend_cards(self, card_ids):
        for card_id in card_ids:
            self.collection.get_card(card_id).queue = -1

    def unsuspend_cards(self, card_ids):
        for card_id in card_ids:
            self.collection.get_card(card_id).queue = 0

    def bury_cards(self, card_ids):
        for card_id in card_ids:
            self.collection.get_card(card_id).queue = -2

    def unbury_cards(self, card_ids):
        for card_id in card_ids:
            self.collection.get_card(card_id).queue = 0

    def set_due_date(self, card_ids, days):
        for card_id in card_ids:
            self.collection.get_card(card_id).due = int(days)

    def reposition_new_cards(self, card_ids, start, step):
        due = int(start)
        for card_id in card_ids:
            self.collection.get_card(card_id).due = due
            due += int(step)

    def deck_due_tree(self, top_deck_id=None):
        root = SimpleNamespace(
            deck_id=0,
            new_count=0,
            learn_count=0,
            review_count=0,
            children=[
                SimpleNamespace(deck_id=1, new_count=1, learn_count=2, review_count=3, children=[]),
                SimpleNamespace(deck_id=2, new_count=4, learn_count=5, review_count=6, children=[]),
            ],
        )
        if top_deck_id is None:
            return root
        for child in root.children:
            if child.deck_id == int(top_deck_id):
                return child
        return None


class FakeSearch:
    def __init__(self) -> None:
        self._text = ""

    def setText(self, value):
        self._text = value

    def text(self):
        return self._text


class FakeBrowser:
    def __init__(self) -> None:
        self.search = FakeSearch()
        self.activated = False
        self.note_selection = []
        self.card_selection = []

    def selectedNotes(self):
        return [1001]

    def selectedCards(self):
        return [2001]

    def onSearchActivated(self):
        self.activated = True

    def selectNotes(self, note_ids):
        self.note_selection = list(note_ids)

    def selectCards(self, card_ids):
        self.card_selection = list(card_ids)

    def show(self):
        pass

    def raise_(self):
        pass


class FakeEditor:
    def __init__(self, note) -> None:
        self.note = note
        self.currentField = 1
        self.loaded = False

    def loadNote(self):
        self.loaded = True
