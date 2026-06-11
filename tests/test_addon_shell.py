import sys
import base64
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from unittest import mock
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
ADDON = ROOT / "addon" / "deckhand"
sys.path.insert(0, str(ADDON))

from deckhand.bridge import BridgeStatus
from deckhand.capabilities import anki_bridge_capability_payload, capability_payload
from deckhand.command_catalog import command_catalog, validate_command_catalog
from deckhand import dev_tools
from deckhand import bridge_transport
from deckhand import import_export_tools
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
from deckhand import tool_visibility
from deckhand import updates
from deckhand import welcome
from deckhand import web
from deckhand import webengine_tools
from deckhand.direct_executor import DirectExecutor
from deckhand.version import ADDON_VERSION


class AddonShellTests(unittest.TestCase):
    def setUp(self):
        self._tool_visibility_tmp = tempfile.TemporaryDirectory()
        self._tool_visibility_patch = mock.patch.object(
            tool_visibility,
            "VISIBILITY_PATH",
            Path(self._tool_visibility_tmp.name) / "tool-visibility.json",
        )
        self._tool_visibility_patch.start()

    def tearDown(self):
        self._tool_visibility_patch.stop()
        self._tool_visibility_tmp.cleanup()

    def test_capability_payload_marks_internal_bridge_path(self):
        payload = capability_payload()
        self.assertEqual(payload["paths"], ["safe_bridge"])
        self.assertIn("anki_run_python", {tool["name"] for tool in payload["tools"]})
        self.assertIn("catalog", payload)
        self.assertEqual(
            len(payload["tools"]),
            len(payload["catalog"]["commands"]),
        )
        self.assertTrue(all(tool["path"] == "safe_bridge" for tool in payload["tools"]))

    def test_anki_bridge_capability_payload_advertises_internal_anki_tools(self):
        payload = anki_bridge_capability_payload()
        names = {tool["name"] for tool in payload["tools"]}

        self.assertEqual(payload["paths"], ["safe_bridge"])
        self.assertIn("anki_app_get_state", names)
        self.assertNotIn("anki_context_get_current", names)
        self.assertIn("anki_note_search", names)
        self.assertIn("anki_run_python", names)
        self.assertNotIn("anki_review_answer_current", names)
        self.assertNotIn("anki_bridge_registry", names)
        self.assertNotIn("anki_bridge_call", names)
        self.assertNotIn("anki_bridge_server_call", names)
        self.assertNotIn("anki_smoke_safe_bridge", names)
        self.assertNotIn("codex.realtime.start_call", names)
        self.assertNotIn("system.exec.run", names)
        self.assertTrue(all(".smoke." not in name for name in names))
        self.assertNotIn("anki_dev_set_addon_config", names)
        self.assertNotIn("anki_dev_backup_collection", names)
        self.assertTrue(all(name.startswith("anki_") for name in names))

    def test_tool_visibility_defaults_to_all_tools(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tool-visibility.json"
            with mock.patch.object(tool_visibility, "VISIBILITY_PATH", path):
                visible = tool_visibility.visible_tool_names(["anki_deck_list", "anki_run_python"])

        self.assertEqual(visible, ["anki_deck_list", "anki_run_python"])

    def test_tool_visibility_template_filters_capability_payload(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "tool-visibility.json"
            with mock.patch.object(tool_visibility, "VISIBILITY_PATH", path):
                tool_visibility.apply_template(tool_visibility.TEMPLATE_RUNTIME_WEBENGINE)
                payload = anki_bridge_capability_payload()

        names = {tool["name"] for tool in payload["tools"]}
        self.assertIn("anki_run_python", names)
        self.assertIn("anki_runtime_info", names)
        self.assertNotIn("anki_note_search", names)
        self.assertNotIn("anki_card_preview", names)
        self.assertNotIn("anki_deck_list", names)
        self.assertEqual(names, {"anki_run_python", "anki_runtime_info"})

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

    def test_addon_menu_exposes_management_and_developer_panel(self):
        source = (ADDON / "deckhand" / "addon.py").read_text(encoding="utf-8")

        self.assertIn('QMenu("Deckhand", mw)', source)
        self.assertIn('QAction("Management", mw)', source)
        self.assertIn('QAction("Developer Panel", mw)', source)
        self.assertIn("developer_panel_action.triggered.connect(show_developer_panel)", source)
        self.assertIn("management.show_developer_panel(mw, _executor.tools(), logger=_log)", source)
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
        self.assertIn("def show_management() -> None:", source)
        self.assertIn("QToolBar", management_source)
        self.assertIn("mw.addToolBar(Qt.ToolBarArea.TopToolBarArea, toolbar)", management_source)
        self.assertNotIn("QDockWidget", management_source)

    def test_management_restart_command_sets_qtwebengine_debug_port(self):
        original = os.environ.get("DECKHAND_ANKI_EXECUTABLE")
        os.environ["DECKHAND_ANKI_EXECUTABLE"] = "/Applications/Anki.app/Contents/MacOS/launcher"
        try:
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

    def test_companion_uses_bundled_server_path_for_platform(self):
        path = companion.bundled_server_path()

        self.assertEqual(path.name, companion.SERVER_BINARY)
        self.assertIn(companion.platform_tag(), path.as_posix())

    def test_companion_ensure_running_reuses_healthy_server(self):
        original_health_status = companion.health_status
        original_started_pid = companion._started_pid
        companion._started_pid = 4321
        companion.health_status = lambda: {"healthy": True, "version": "0.1.0"}
        try:
            status = companion.ensure_running()
        finally:
            companion.health_status = original_health_status
            companion._started_pid = original_started_pid

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
        original_start_companion = companion.start_companion
        with tempfile.TemporaryDirectory() as temp_dir:
            binary = Path(temp_dir) / companion.SERVER_BINARY
            binary.write_text("#!/bin/sh\n", encoding="utf-8")
            checks = iter(
                [
                    {"healthy": False, "error": "connection refused"},
                    {"healthy": True, "version": "0.1.0"},
                ]
            )
            companion.health_status = lambda: next(checks, {"healthy": True, "version": "0.1.0"})
            companion.bundled_server_path = lambda: binary
            companion.start_companion = lambda path, logger=None: calls.append(path) or FakeProcess()
            try:
                status = companion.ensure_running()
            finally:
                companion.health_status = original_health_status
                companion.bundled_server_path = original_bundled_server_path
                companion.start_companion = original_start_companion

        self.assertEqual(calls, [binary])
        self.assertEqual(status["state"], "running")
        self.assertTrue(status["ownedByAnki"])
        self.assertEqual(status["pid"], 9876)

    def test_companion_ensure_running_reports_stale_unowned_server(self):
        original_health_status = companion.health_status
        original_read_pid_file = companion.read_pid_file
        original_stop_recorded_companion = companion.stop_recorded_companion
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

        self.assertEqual(status["state"], "stale")
        self.assertFalse(status["ownedByAnki"])
        self.assertEqual(status["health"]["staleReasons"], ["unexpected_service"])

    def test_companion_status_compatibility_only_checks_service_identity(self):
        original_urlopen = companion.urlopen

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                return False

            def read(self):
                return b'{"service":"deckhand-anki-companion","ready":true,"endpoints":["/api/codex/session/start"]}'

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

    def test_lens_inspector_is_removed_from_management(self):
        config = json.loads((ADDON / "config.json").read_text(encoding="utf-8"))
        management_source = (ADDON / "deckhand" / "management.py").read_text(encoding="utf-8")
        management_body = management_source[
            management_source.index("def _build_management_dialog") : management_source.index("def mcp_install_instructions")
        ]

        self.assertNotIn("enable_lens_inspector", config)
        self.assertFalse((ADDON / "deckhand" / "anki_lens").exists())
        self.assertNotIn('QCheckBox("Enable Lens Inspector")', management_body)
        self.assertIn('dialog.setWindowTitle("Deckhand")', management_body)
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

        self.assertIn('tabs.addTab(_build_connect_tab(tabs, logger=logger), "Connect")', source)
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
        self.assertIn('tabs.addTab(_build_tools_tab(tabs, anki_tools), "Tools")', source)
        self.assertIn('tabs.addTab(_build_connection_tab(tabs, anki_tools), "Connection")', source)
        self.assertIn('tabs.addTab(_build_webengine_tab(tabs, logger=logger), "WebEngine")', source)
        self.assertIn('tabs.addTab(_build_logs_tab(tabs, anki_tools), "Logs")', source)
        self.assertIn('search.setPlaceholderText("Search tools by name, namespace, or description")', source)
        self.assertIn('QPushButton("All tools")', source)
        self.assertIn('QPushButton("Runtime + WebEngine")', source)
        self.assertIn('QPushButton("Save visibility")', source)
        self.assertIn("tool_visibility.save_visible_tool_names(visible)", source)
        self.assertIn("QPlainTextEdit", source)

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

    def test_connect_recipes_cover_target_clients(self):
        url = "http://127.0.0.1:28765/mcp"

        desktop = management.connect_recipe(management.CLIENT_CLAUDE_DESKTOP, url)
        self.assertEqual(desktop["snippet"], url)
        self.assertTrue(any("custom connector" in step for step in desktop["steps"]))

        code = management.connect_recipe(management.CLIENT_CLAUDE_CODE, url)
        self.assertEqual(code["snippet"], f"claude mcp add --transport http deckhand {url}")

        code_with_token = management.connect_recipe(management.CLIENT_CLAUDE_CODE, url, "tok123")
        self.assertIn('--header "Authorization: Bearer tok123"', code_with_token["snippet"])

        codex = management.connect_recipe(management.CLIENT_CODEX, url, "tok123")
        self.assertIn("[mcp_servers.deckhand]", codex["snippet"])
        self.assertIn(f'url = "{url}"', codex["snippet"])
        self.assertIn("Bearer tok123", codex["snippet"])

        other = management.connect_recipe("unknown-client", url)
        self.assertEqual(other["snippet"], url)
        self.assertTrue(any("Streamable HTTP" in step for step in other["steps"]))

    def test_claude_desktop_recipe_warns_when_token_required(self):
        recipe = management.connect_recipe(management.CLIENT_CLAUDE_DESKTOP, "http://127.0.0.1:28765/mcp", "tok123")

        self.assertTrue(any("Require access token" in step for step in recipe["steps"]))
        self.assertNotIn("tok123", recipe["snippet"])

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

        self.assertEqual(manifest["human_version"], ADDON_VERSION)
        self.assertIn('"addonVersion": ADDON_VERSION,', addon_source)
        self.assertNotIn('"addonVersion": "0.', addon_source)

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

    def test_state_root_never_defaults_to_dev_checkout_on_user_machines(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DECKHAND_ANKI_EXTENSION_STATE_ROOT", None)
            with mock.patch.object(state_paths, "LEGACY_DEV_STATE_ROOT", Path("/nonexistent/deckhand-state")):
                root = state_paths.state_root()

        self.assertEqual(root, state_paths.default_state_root())
        self.assertNotIn("github.com", str(root))

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
        self.assertIn("skills_updates.start_background_sync(mw, logger=_log)", source)

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
        run_python_description = entries["anki_run_python"].description
        runtime_description = entries["anki_runtime_info"].description
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
        original = dev_tools._anki_snippet_globals
        dev_tools._anki_snippet_globals = lambda: {
            "__builtins__": {"len": len},
            "mw": object(),
            "result": None,
        }
        try:
            result = dev_tools.run_python_snippet("result = len('mcp')")
        finally:
            dev_tools._anki_snippet_globals = original

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
        original = dev_tools._anki_snippet_globals
        dev_tools._anki_snippet_globals = lambda: {
            "__builtins__": {"len": len, "str": str},
            "mw": object(),
            "result": None,
        }
        try:
            result = dev_tools.run_python_snippet("result = len(str(mw))")
        finally:
            dev_tools._anki_snippet_globals = original

        self.assertIn("result", result)
        self.assertIsInstance(result["result"], int)

    def test_snippet_executes_normal_imports(self):
        original = dev_tools._anki_snippet_globals
        dev_tools._anki_snippet_globals = lambda: {
            "__builtins__": __import__("builtins"),
            "mw": object(),
            "result": None,
        }
        try:
            result = dev_tools.run_python_snippet("import math\nresult = math.ceil(1.2)")
        finally:
            dev_tools._anki_snippet_globals = original

        self.assertEqual(result["result"], 2)

    def test_snippet_omits_large_result_without_mutating_result_data(self):
        original = dev_tools._anki_snippet_globals
        dev_tools._anki_snippet_globals = lambda: {
            "__builtins__": {"len": len},
            "mw": object(),
            "result": None,
        }
        try:
            result = dev_tools.run_python_snippet("result = 'x' * 20", inline_limit_bytes=10)
        finally:
            dev_tools._anki_snippet_globals = original

        self.assertIsNone(result["result"])
        self.assertFalse(result["resultInline"])
        self.assertFalse(result["resultTruncated"])
        self.assertTrue(result["resultOmitted"])
        self.assertIn('"xxxxxxxxxxxxxxxxxxxx"', result["resultPreview"])
        self.assertIn("rerun with resultFilePath", result["message"])

    def test_snippet_writes_large_result_artifact(self):
        original = dev_tools._anki_snippet_globals
        dev_tools._anki_snippet_globals = lambda: {
            "__builtins__": {"len": len},
            "mw": object(),
            "result": None,
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "result.json"
            try:
                result = dev_tools.run_python_snippet(
                    "result = {'text': 'x' * 20}",
                    result_file_path=str(path),
                    inline_limit_bytes=1,
                )
            finally:
                dev_tools._anki_snippet_globals = original

            self.assertIsNone(result["result"])
            self.assertFalse(result["resultInline"])
            self.assertFalse(result["resultOmitted"])
            self.assertEqual(result["artifact"]["path"], str(path))
            self.assertEqual(result["artifact"]["format"], "json")
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")), {"text": "xxxxxxxxxxxxxxxxxxxx"})

    def test_snippet_text_result_format_artifact(self):
        original = dev_tools._anki_snippet_globals
        dev_tools._anki_snippet_globals = lambda: {
            "__builtins__": {},
            "mw": object(),
            "result": None,
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "result.txt"
            try:
                result = dev_tools.run_python_snippet(
                    "result = 'plain text'",
                    result_file_path=str(path),
                    result_format="text",
                )
            finally:
                dev_tools._anki_snippet_globals = original

            self.assertEqual(path.read_text(encoding="utf-8"), "plain text")
            self.assertEqual(result["artifact"]["format"], "text")

    def test_snippet_unserializable_result_falls_back_to_repr(self):
        original = dev_tools._anki_snippet_globals
        dev_tools._anki_snippet_globals = lambda: {
            "__builtins__": {"object": object},
            "mw": object(),
            "result": None,
        }
        try:
            result = dev_tools.run_python_snippet("result = object()")
        finally:
            dev_tools._anki_snippet_globals = original

        self.assertIn("object object", result["result"])

    def test_snippet_blocks_builtin_exit(self):
        original = dev_tools._anki_snippet_globals
        dev_tools._anki_snippet_globals = lambda: {
            "__builtins__": {"exit": dev_tools._blocked_callable("exit")},
            "mw": object(),
            "result": None,
        }
        try:
            with self.assertRaises(dev_tools.DevToolError) as context:
                dev_tools.run_python_snippet("exit()")
        finally:
            dev_tools._anki_snippet_globals = original

        self.assertEqual(str(context.exception), "snippet_forbidden_operation:exit")

    def test_snippet_blocks_sys_exit(self):
        original = dev_tools._anki_snippet_globals
        dev_tools._anki_snippet_globals = lambda: {
            "__builtins__": {
                "__import__": dev_tools._guarded_import(
                    {"sys": dev_tools.GuardedModuleProxy(__import__("sys"), {"exit": "sys.exit"})}
                )
            },
            "mw": object(),
            "result": None,
        }
        try:
            with self.assertRaises(dev_tools.DevToolError) as context:
                dev_tools.run_python_snippet("import sys\nsys.exit(2)")
        finally:
            dev_tools._anki_snippet_globals = original

        self.assertEqual(str(context.exception), "snippet_forbidden_operation:sys.exit")

    def test_snippet_normalizes_system_exit(self):
        original = dev_tools._anki_snippet_globals
        dev_tools._anki_snippet_globals = lambda: {
            "__builtins__": __import__("builtins"),
            "mw": object(),
            "result": None,
        }
        try:
            with self.assertRaises(dev_tools.DevToolError) as context:
                dev_tools.run_python_snippet("raise SystemExit(3)")
        finally:
            dev_tools._anki_snippet_globals = original

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
        source = Path("/private/tmp/deckhand-anki-tests/media/unsafe sample!.txt")
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
        source = Path("/private/tmp/deckhand-anki-tests/media/update path.txt")
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
        artifact_dir = Path("/private/tmp/deckhand-anki-tests/import-export")
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
        self.path = str(Path("/private/tmp/deckhand-anki-tests/fake-collection.anki2"))
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
        self.root = Path("/private/tmp/deckhand-anki-tests/fake-media")
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
