from __future__ import annotations

import json
import hashlib
import os
import threading
import time
from pathlib import Path

from . import bridge_transport
from . import anki_lens
from . import card_tools
from . import companion
from . import context_tools
from . import dev_tools
from . import import_export_tools
from . import management
from . import media_tools
from . import structure_tools
from . import typed_tools
from . import ui_tools
from . import webengine_tools
from .bridge import bridge_status
from .capabilities import anki_bridge_capability_payload, capability_payload
from .direct_executor import DirectExecutor
from .state_paths import work_root

_menu = None
_windows: dict[str, object] = {}
_executor = DirectExecutor()
_attachment_store = media_tools.AttachmentStore()
_safe_bridge_client = None
_companion_shutdown_hook_installed = False
_fallback_store = typed_tools.FakeNoteStore(
    [
        typed_tools.NoteRecord(
            id=1,
            fields={"Front": "Deckhand prototype", "Back": "Native Anki add-on"},
            tags=["deckhand"],
        )
    ]
)
_MAX_EVIDENCE_LOG_BYTES = 5 * 1024 * 1024
_TRIMMED_EVIDENCE_LOG_BYTES = 2 * 1024 * 1024


def _evidence_log_path() -> Path:
    return work_root() / "logs" / "addon-shell.jsonl"


def _log(event: str, **payload: object) -> None:
    path = _evidence_log_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.stat().st_size > _MAX_EVIDENCE_LOG_BYTES:
            tail = path.read_bytes()[-_TRIMMED_EVIDENCE_LOG_BYTES:]
            newline = tail.find(b"\n")
            if newline >= 0:
                tail = tail[newline + 1 :]
            path.write_bytes(tail)
        record = {"event": event, "createdAtMs": int(time.time() * 1000), **payload}
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    except OSError:
        return


def _start_safe_bridge_transport() -> None:
    global _safe_bridge_client
    if _safe_bridge_client is not None:
        return
    _safe_bridge_client = bridge_transport.SafeBridgeClient(
        executor=_executor,
        registry_provider=_bridge_registry,
        logger=_log,
        executor_runner=_call_executor_on_main,
    )
    _safe_bridge_client.start()


def _call_executor_on_main(tool: str, arguments: dict[str, object]) -> dict[str, object]:
    if tool.startswith("anki.webengine."):
        return _executor.call(tool, arguments).to_dict()

    try:
        from aqt import mw
        from aqt.qt import QApplication, QThread
    except Exception:
        return _executor.call(tool, arguments).to_dict()

    app = QApplication.instance()
    if app is None or app.thread() == QThread.currentThread():
        return _executor.call(tool, arguments).to_dict()

    event = threading.Event()
    box: dict[str, object] = {}

    def run() -> None:
        try:
            box["result"] = _executor.call(tool, arguments).to_dict()
        except Exception as exc:  # noqa: BLE001 - preserve bridge liveness
            box["result"] = {
                "ok": False,
                "result": None,
                "error": f"main_thread_dispatch_failed: {exc}",
                "durationMs": 0,
            }
        finally:
            event.set()

    mw.taskman.run_on_main(run)
    if not event.wait(20):
        return {
            "ok": False,
            "result": None,
            "error": "main_thread_dispatch_timeout",
            "durationMs": 0,
        }
    return dict(box.get("result") or {})


def setup() -> None:
    try:
        from aqt import mw
    except Exception as exc:  # pragma: no cover - only meaningful inside Anki
        _log("addon.setup_failed", error=str(exc))
        return

    _register_default_tools()
    companion.ensure_running(logger=_log)
    _install_companion_shutdown_hook()
    _start_safe_bridge_transport()
    _install_menu(mw)
    anki_lens.setup("deckhand")
    management.maybe_show_cdp_banner(mw, logger=_log)

    _log(
        "addon.loaded",
        capabilities=capability_payload(),
        bridge=bridge_status.to_dict(),
    )


def _install_companion_shutdown_hook() -> None:
    global _companion_shutdown_hook_installed
    if _companion_shutdown_hook_installed:
        return
    try:
        from aqt import gui_hooks
    except Exception as exc:  # pragma: no cover - only meaningful inside Anki
        _log("companion.shutdown_hook_unavailable", error=str(exc))
        return

    hook = getattr(gui_hooks, "profile_will_close", None)
    if hook is None:
        _log("companion.shutdown_hook_missing", hook="profile_will_close")
        return
    hook.append(lambda *args: companion.stop_started_companion(logger=_log))
    _companion_shutdown_hook_installed = True
    _log("companion.shutdown_hook_installed")


def _install_menu(mw) -> None:
    global _menu
    try:
        from aqt.qt import QAction, QMenu
    except Exception as exc:  # pragma: no cover - only meaningful inside Anki
        _log("addon.menu_failed", error=str(exc))
        return

    if _menu is not None:
        return

    menu = QMenu("Deckhand", mw)
    menu.setObjectName("deckhand_menu")

    management_action = QAction("Management", mw)
    management_action.triggered.connect(show_management)
    menu.addAction(management_action)

    developer_menu = QMenu("Developer", mw)
    developer_menu.setObjectName("deckhand_developer_menu")

    diagnostics_action = QAction("Diagnostics", mw)
    diagnostics_action.triggered.connect(show_diagnostics)
    developer_menu.addAction(diagnostics_action)

    bridge_status_action = QAction("Bridge Status", mw)
    bridge_status_action.triggered.connect(show_bridge_status)
    developer_menu.addAction(bridge_status_action)

    developer_tools_action = QAction("Developer Tools", mw)
    developer_tools_action.triggered.connect(show_developer_tools)
    developer_menu.addAction(developer_tools_action)

    menu.addMenu(developer_menu)

    menu_bar = mw.form.menubar
    help_menu = getattr(mw.form, "menuHelp", None)
    if help_menu is not None:
        menu_bar.insertMenu(help_menu.menuAction(), menu)
    else:
        menu_bar.addMenu(menu)

    _menu = menu
    _log("addon.menu_installed")


def _register_default_tools() -> None:
    _executor.register(
        "anki.execute",
        lambda args: dev_tools.run_python_snippet(
            str(args.get("snippet", "")),
        ),
    )
    _executor.register("anki.webengine.status", lambda args: webengine_tools.status(args.get("host"), args.get("port"), float(args.get("timeoutSeconds", 2.0))))
    _executor.register("anki.webengine.list_pages", lambda args: webengine_tools.list_pages(args.get("host"), args.get("port"), float(args.get("timeoutSeconds", 2.0))))
    _executor.register("anki.webengine.take_snapshot", lambda args: webengine_tools.take_snapshot(args))
    _executor.register("anki.webengine.take_screenshot", lambda args: webengine_tools.take_screenshot(args))
    _executor.register("anki.webengine.evaluate_script", lambda args: webengine_tools.evaluate_script(args))
    _executor.register("anki.webengine.click", lambda args: webengine_tools.click(args))
    _executor.register("anki.webengine.type_text", lambda args: webengine_tools.type_text(args))
    _executor.register("anki.webengine.press_key", lambda args: webengine_tools.press_key(args))
    _executor.register("anki.webengine.wait_for", lambda args: webengine_tools.wait_for(args))
    _executor.register("anki.webengine.list_console_messages", lambda args: webengine_tools.list_console_messages(args))
    _executor.register("anki.webengine.list_network_requests", lambda args: webengine_tools.list_network_requests(args))
    _executor.register("anki.webengine.send_cdp_command", lambda args: webengine_tools.send_cdp_command(args))
    _executor.register(
        "anki.context.get_current", lambda _args: context_tools.current_context(_mw())
    )
    _executor.register(
        "anki.context.get_selection", lambda _args: context_tools.current_selection(_mw())
    )
    _executor.register(
        "anki.context.get_profile", lambda _args: context_tools.current_profile(_mw())
    )
    _executor.register(
        "anki.context.get_deck_browser", lambda _args: context_tools.deck_browser_state(_mw())
    )
    _executor.register(
        "anki.navigate.deck_browser", lambda _args: _navigation_result("anki.navigate.deck_browser", context_tools.navigate_deck_browser(_mw()))
    )
    _executor.register(
        "anki.navigate.browser_search",
        lambda args: _navigation_result(
            "anki.navigate.browser_search",
            context_tools.navigate_browser_search(_mw(), str(args.get("query", ""))),
        ),
    )
    _executor.register(
        "anki.navigate.note",
        lambda args: _navigation_result(
            "anki.navigate.note",
            context_tools.navigate_note(_mw(), int(args.get("noteId"))),
        ),
    )
    _executor.register(
        "anki.navigate.card",
        lambda args: _navigation_result(
            "anki.navigate.card",
            context_tools.navigate_card(_mw(), int(args.get("cardId"))),
        ),
    )
    _executor.register(
        "anki.note.search",
        lambda args: typed_tools.note_search(
            _note_store(), str(args.get("query", "")), int(args.get("limit", 20))
        ),
    )
    _executor.register(
        "anki.note.get",
        lambda args: typed_tools.note_get(_note_store(), int(args.get("noteId"))),
    )
    _executor.register(
        "anki.note.update_fields",
        lambda args: typed_tools.note_update_fields(
            _note_store(),
            int(args.get("noteId")),
            dict(args.get("fields", {})),
        ),
    )
    _executor.register(
        "anki.note.add_tag",
        lambda args: typed_tools.note_add_tag(
            _note_store(),
            int(args.get("noteId")),
            str(args.get("tag", "")),
        ),
    )
    _executor.register(
        "anki.note.create",
        lambda args: typed_tools.note_create(
            _note_store(),
            str(args.get("deck", "")),
            str(args.get("model", "")),
            dict(args.get("fields", {})),
            list(args.get("tags", [])),
        ),
    )
    _executor.register(
        "anki.note.remove_tag",
        lambda args: typed_tools.note_remove_tag(
            _note_store(),
            int(args.get("noteId")),
            str(args.get("tag", "")),
        ),
    )
    _executor.register(
        "anki.note.set_tags",
        lambda args: typed_tools.note_set_tags(
            _note_store(),
            int(args.get("noteId")),
            list(args.get("tags", [])),
        ),
    )
    _executor.register(
        "anki.note.delete",
        lambda args: typed_tools.note_delete(
            _note_store(),
            list(args.get("noteIds", [])),
            int(args.get("cap", 20)),
        ),
    )
    _executor.register(
        "anki.card.get", lambda args: card_tools.card_get(_mw(), int(args.get("cardId")))
    )
    _executor.register(
        "anki.card.find_by_note",
        lambda args: card_tools.card_find_by_note(_mw(), int(args.get("noteId"))),
    )
    _executor.register(
        "anki.card.preview",
        lambda args: card_tools.card_preview(_mw(), int(args.get("cardId"))),
    )
    _executor.register(
        "anki.card.suspend",
        lambda args: card_tools.card_suspend(_mw(), list(args.get("cardIds", []))),
    )
    _executor.register(
        "anki.card.unsuspend",
        lambda args: card_tools.card_unsuspend(_mw(), list(args.get("cardIds", []))),
    )
    _executor.register(
        "anki.card.bury",
        lambda args: card_tools.card_bury(_mw(), list(args.get("cardIds", []))),
    )
    _executor.register(
        "anki.card.unbury",
        lambda args: card_tools.card_unbury(_mw(), list(args.get("cardIds", []))),
    )
    _executor.register(
        "anki.card.set_due",
        lambda args: card_tools.card_set_due(
            _mw(),
            list(args.get("cardIds", [])),
            int(args.get("days", 0)),
        ),
    )
    _executor.register(
        "anki.review.answer_current",
        lambda args: card_tools.review_answer_current(_mw(), int(args.get("ease", 1))),
    )
    _executor.register("anki.deck.list", lambda _args: structure_tools.deck_list(_mw()))
    _executor.register(
        "anki.deck.get_stats",
        lambda args: structure_tools.deck_get_stats(_mw(), args.get("deckId")),
    )
    _executor.register(
        "anki.deck.create",
        lambda args: structure_tools.deck_create(_mw(), str(args.get("name", ""))),
    )
    _executor.register("anki.model.list", lambda _args: structure_tools.model_list(_mw()))
    _executor.register(
        "anki.model.get",
        lambda args: structure_tools.model_get(_mw(), args.get("modelName"), args.get("modelId")),
    )
    _executor.register(
        "anki.media.add_file",
        lambda args: media_tools.add_file(
            _mw(),
            _attachment_store,
            str(args.get("path", "")),
            source_kind=str(args.get("sourceKind", "user_input")),
            provenance=dict(args.get("provenance", {})),
        ),
    )
    _executor.register(
        "anki.media.add_url",
        lambda args: media_tools.add_url(
            _mw(),
            _attachment_store,
            str(args.get("url", "")),
            source_kind=str(args.get("sourceKind", "remote_url")),
        ),
    )
    _executor.register(
        "anki.media.get",
        lambda args: media_tools.get(_mw(), str(args.get("filename", ""))),
    )
    _executor.register(
        "anki.media.attach_to_field",
        lambda args: media_tools.attach_to_field(
            _mw(),
            int(args.get("noteId")),
            str(args.get("field", "")),
            str(args.get("filename", "")),
            media_type=args.get("mediaType"),
        ),
    )
    _executor.register(
        "anki.export.notes",
        lambda args: import_export_tools.export_notes(
            _mw(),
            query=str(args.get("query", "")),
            filePath=args.get("filePath"),
            format=str(args.get("format", "csv")),
            limit=int(args.get("limit", 100)),
            overwrite=bool(args.get("overwrite", False)),
        ),
    )
    _executor.register(
        "anki.export.deck_snapshot",
        lambda args: import_export_tools.deck_snapshot(
            _mw(),
            filePath=args.get("filePath"),
            overwrite=bool(args.get("overwrite", False)),
        ),
    )
    _executor.register(
        "anki.export.deck_package",
        lambda args: import_export_tools.export_deck_package(
            _mw(),
            filePath=args.get("filePath"),
            deck=args.get("deck"),
            includeMedia=bool(args.get("includeMedia", True)),
            includeScheduling=bool(args.get("includeScheduling", False)),
            overwrite=bool(args.get("overwrite", False)),
        ),
    )
    _executor.register(
        "anki.export.collection_package",
        lambda args: import_export_tools.export_collection_package(
            _mw(),
            filePath=args.get("filePath"),
            includeMedia=bool(args.get("includeMedia", True)),
            legacy=bool(args.get("legacy", False)),
            overwrite=bool(args.get("overwrite", False)),
        ),
    )
    _executor.register(
        "anki.backup.create",
        lambda args: import_export_tools.backup_create(
            _mw(),
            folderPath=args.get("folderPath"),
            force=bool(args.get("force", True)),
            waitForCompletion=bool(args.get("waitForCompletion", True)),
        ),
    )
    _executor.register(
        "anki.browser.search",
        lambda args: ui_tools.browser_search(_mw(), str(args.get("query", "")), int(args.get("limit", 50))),
    )
    _executor.register(
        "anki.browser.apply_tags",
        lambda args: ui_tools.browser_apply_tags(_mw(), list(args.get("tags", []))),
    )
    _executor.register("anki.editor.get_focused_note", lambda _args: ui_tools.editor_get_focused_note(_mw()))
    _executor.register(
        "anki.editor.set_field",
        lambda args: ui_tools.editor_set_field(_mw(), str(args.get("field", "")), str(args.get("value", ""))),
    )
    _executor.register(
        "anki.editor.insert_media",
        lambda args: ui_tools.editor_insert_media(_mw(), str(args.get("filename", "")), args.get("field")),
    )


def _mw():
    from aqt import mw

    return mw


def _navigation_result(event: str, result: dict[str, object]) -> dict[str, object]:
    _log(event, **result)
    return result


def _note_store() -> typed_tools.NoteStore:
    return typed_tools.collection_store_from_anki() or _fallback_store


def _bridge_registry() -> dict[str, object]:
    payload = anki_bridge_capability_payload()
    profile = context_tools.current_profile(_mw())
    profile_name = str(profile.get("name") or "anki-local-profile")
    collection_base = str(profile.get("base") or "")
    return {
        "bridgeId": "anki-local-profile",
        "protocol": "deckhand.safe_bridge.v1",
        "protocolVersion": "deckhand.ankiBridge.v1",
        "addonVersion": "0.1.0",
        "profileHash": hashlib.sha256(profile_name.encode("utf-8")).hexdigest()[:16],
        "collectionHash": hashlib.sha256(collection_base.encode("utf-8")).hexdigest()[:16] if collection_base else None,
        "status": bridge_status.to_dict(),
        "capabilities": payload,
        "tools": payload["tools"],
    }


def diagnostics() -> dict[str, object]:
    return dev_tools.diagnostics(
        capabilities=capability_payload(),
        bridge=bridge_status.to_dict(),
        anki_tools=_executor.tools(),
    )


def show_management() -> None:
    try:
        from aqt import mw
    except Exception as exc:  # pragma: no cover - only meaningful inside Anki/Qt
        _log("management.unavailable", error=str(exc))
        return
    management.show_management_dialog(mw, _executor.tools(), logger=_log)


def show_developer_tools() -> None:
    try:
        from aqt import mw
    except Exception as exc:  # pragma: no cover - only meaningful inside Anki/Qt
        _log("developer_tools.unavailable", error=str(exc))
        return
    management.show_developer_dialog(mw, _executor.tools(), logger=_log)


def show_diagnostics() -> None:
    _show_text_dialog("Deckhand Diagnostics", json.dumps(diagnostics(), indent=2, sort_keys=True))


def show_bridge_status() -> None:
    bridge = bridge_status.to_dict()
    text = "\n".join(
        [
            f"State: {bridge.get('state', 'unknown')}",
            f"Detail: {bridge.get('detail', '')}",
            f"Last change: {bridge.get('last_change_ms', '')}",
            "",
            f"Anki tools: {len(_executor.tools())}",
        ]
    )
    _show_text_dialog("Deckhand Bridge Status", text)


def _show_web_window(key: str, title: str, html_name: str) -> None:
    global _windows
    existing = _windows.get(key)
    if existing is not None:
        try:
            existing.show()
            existing.raise_()
            existing.activateWindow()
            return
        except Exception:
            _windows.pop(key, None)

    try:
        from aqt import mw
        from aqt.qt import QDialog, QUrl, QVBoxLayout, QWebEngineView
    except Exception as exc:  # pragma: no cover - fallback for Anki builds without WebEngine
        _log("menu.web_window_unavailable", key=key, error=str(exc))
        _show_text_dialog(title, f"{title} is unavailable because Qt WebEngine is not available.\n\n{exc}")
        return

    dialog = QDialog(mw)
    dialog.setWindowTitle(title)
    dialog.resize(920, 640)
    layout = QVBoxLayout(dialog)
    view = QWebEngineView(dialog)
    layout.addWidget(view)
    html_path = Path(__file__).resolve().parent / "web" / html_name
    view.load(QUrl.fromLocalFile(str(html_path)))
    dialog.finished.connect(lambda _result, window_key=key: _windows.pop(window_key, None))
    _windows[key] = dialog
    dialog.show()
    _log("menu.web_window_opened", key=key, htmlPath=str(html_path))


def _show_text_dialog(title: str, text: str) -> None:
    try:
        from aqt import mw
        from aqt.qt import QDialog, QPlainTextEdit, QVBoxLayout
    except Exception as exc:  # pragma: no cover - only meaningful inside Anki/Qt
        _log("menu.text_dialog_unavailable", title=title, error=str(exc), text=text)
        return

    dialog = QDialog(mw)
    dialog.setWindowTitle(title)
    dialog.resize(760, 560)
    layout = QVBoxLayout(dialog)
    editor = QPlainTextEdit(dialog)
    editor.setReadOnly(True)
    editor.setPlainText(text)
    layout.addWidget(editor)
    key = f"text:{title}"
    dialog.finished.connect(lambda _result, window_key=key: _windows.pop(window_key, None))
    _windows[key] = dialog
    dialog.show()
    _log("menu.text_dialog_opened", title=title)
