from __future__ import annotations

import json
import hashlib
import os
import threading
import time
from pathlib import Path

from . import bridge_transport
from . import card_tools
from . import companion
from . import context_tools
from . import import_export_tools
from . import management
from . import media_tools
from . import runtime_tools
from . import structure_tools
from . import typed_tools
from . import skills_updates
from . import updates
from . import welcome
from .bridge import bridge_status
from .capabilities import anki_bridge_capability_payload, capability_payload
from .direct_executor import DirectExecutor
from .state_paths import work_root
from .version import ADDON_VERSION

_menu = None
_executor = DirectExecutor()
_attachment_store = media_tools.AttachmentStore()
_safe_bridge_client = None
_companion_shutdown_hook_installed = False
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
    if not welcome.maybe_show_welcome(mw, open_setup=show_management, logger=_log):
        # First launch gets the splash instead; the banner can wait a session.
        management.maybe_show_cdp_banner(mw, logger=_log)
    updates.start_background_check(mw, logger=_log)
    skills_updates.start_background_sync(mw, logger=_log)

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

    onboarding_action = QAction("Onboarding", mw)
    onboarding_action.triggered.connect(show_onboarding)
    menu.addAction(onboarding_action)

    management_action = QAction("Management", mw)
    management_action.triggered.connect(show_management)
    menu.addAction(management_action)

    menu_bar = mw.form.menubar
    help_menu = getattr(mw.form, "menuHelp", None)
    if help_menu is not None:
        menu_bar.insertMenu(help_menu.menuAction(), menu)
    else:
        menu_bar.addMenu(menu)

    _menu = menu
    _log("addon.menu_installed")


def _register_default_tools() -> None:
    _executor.register("anki_run_python", _run_python_snippet)
    _executor.register("anki_runtime_info", lambda _args: runtime_tools.runtime_info(_mw()))
    _executor.register(
        "anki_app_get_state", lambda _args: context_tools.current_context(_mw())
    )
    _executor.register(
        "anki_context_get_profile", lambda _args: context_tools.current_profile(_mw())
    )
    _executor.register(
        "anki_note_search",
        lambda args: typed_tools.note_search(
            _note_store(), str(args.get("query", "")), int(args.get("limit", 20))
        ),
    )
    _executor.register(
        "anki_note_get",
        lambda args: typed_tools.note_get(_note_store(), int(args.get("noteId"))),
    )
    _executor.register(
        "anki_note_update_fields",
        lambda args: typed_tools.note_update_fields(
            _note_store(),
            int(args.get("noteId")),
            dict(args.get("fields", {})),
        ),
    )
    _executor.register(
        "anki_note_add_tag",
        lambda args: typed_tools.note_add_tag(
            _note_store(),
            int(args.get("noteId")),
            str(args.get("tag", "")),
        ),
    )
    _executor.register(
        "anki_note_create",
        lambda args: typed_tools.note_create(
            _note_store(),
            str(args.get("deck", "")),
            str(args.get("model", "")),
            dict(args.get("fields", {})),
            list(args.get("tags", [])),
        ),
    )
    _executor.register(
        "anki_note_remove_tag",
        lambda args: typed_tools.note_remove_tag(
            _note_store(),
            int(args.get("noteId")),
            str(args.get("tag", "")),
        ),
    )
    _executor.register(
        "anki_note_set_tags",
        lambda args: typed_tools.note_set_tags(
            _note_store(),
            int(args.get("noteId")),
            list(args.get("tags", [])),
        ),
    )
    _executor.register(
        "anki_note_delete",
        lambda args: typed_tools.note_delete(
            _note_store(),
            list(args.get("noteIds", [])),
            int(args.get("cap", 20)),
        ),
    )
    _executor.register(
        "anki_card_get", lambda args: card_tools.card_get(_mw(), int(args.get("cardId")))
    )
    _executor.register(
        "anki_card_find_by_note",
        lambda args: card_tools.card_find_by_note(_mw(), int(args.get("noteId"))),
    )
    _executor.register(
        "anki_card_preview",
        lambda args: card_tools.card_preview(_mw(), int(args.get("cardId"))),
    )
    _executor.register(
        "anki_card_suspend",
        lambda args: card_tools.card_suspend(_mw(), list(args.get("cardIds", []))),
    )
    _executor.register(
        "anki_card_unsuspend",
        lambda args: card_tools.card_unsuspend(_mw(), list(args.get("cardIds", []))),
    )
    _executor.register(
        "anki_card_bury",
        lambda args: card_tools.card_bury(_mw(), list(args.get("cardIds", []))),
    )
    _executor.register(
        "anki_card_unbury",
        lambda args: card_tools.card_unbury(_mw(), list(args.get("cardIds", []))),
    )
    _executor.register(
        "anki_card_set_due",
        lambda args: card_tools.card_set_due(
            _mw(),
            list(args.get("cardIds", [])),
            int(args.get("days", 0)),
        ),
    )
    _executor.register("anki_deck_list", lambda _args: structure_tools.deck_list(_mw()))
    _executor.register(
        "anki_deck_get_stats",
        lambda args: structure_tools.deck_get_stats(_mw(), args.get("deckId")),
    )
    _executor.register(
        "anki_deck_create",
        lambda args: structure_tools.deck_create(_mw(), str(args.get("name", ""))),
    )
    _executor.register("anki_model_list", lambda _args: structure_tools.model_list(_mw()))
    _executor.register(
        "anki_model_get",
        lambda args: structure_tools.model_get(_mw(), args.get("modelName"), args.get("modelId")),
    )
    _executor.register(
        "anki_media_add_file",
        lambda args: media_tools.add_file(
            _mw(),
            _attachment_store,
            str(args.get("path", "")),
            source_kind=str(args.get("sourceKind", "user_input")),
            provenance=dict(args.get("provenance", {})),
        ),
    )
    _executor.register(
        "anki_media_get",
        lambda args: media_tools.get(_mw(), str(args.get("filename", ""))),
    )
    _executor.register(
        "anki_media_attach_to_field",
        lambda args: media_tools.attach_to_field(
            _mw(),
            int(args.get("noteId")),
            str(args.get("field", "")),
            str(args.get("filename", "")),
            media_type=args.get("mediaType"),
        ),
    )
    _executor.register(
        "anki_export_notes",
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
        "anki_export_deck_snapshot",
        lambda args: import_export_tools.deck_snapshot(
            _mw(),
            filePath=args.get("filePath"),
            overwrite=bool(args.get("overwrite", False)),
        ),
    )
    _executor.register(
        "anki_export_deck_package",
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
        "anki_export_collection_package",
        lambda args: import_export_tools.export_collection_package(
            _mw(),
            filePath=args.get("filePath"),
            includeMedia=bool(args.get("includeMedia", True)),
            overwrite=bool(args.get("overwrite", False)),
        ),
    )
    _executor.register(
        "anki_backup_create",
        lambda args: import_export_tools.backup_create(
            _mw(),
            folderPath=args.get("folderPath"),
            force=bool(args.get("force", True)),
            waitForCompletion=bool(args.get("waitForCompletion", True)),
        ),
    )


def _run_python_snippet(args: dict[str, object]) -> dict[str, object]:
    from . import runtime_snippets

    return runtime_snippets.run_python_snippet(
        str(args.get("snippet", "")),
        result_file_path=str(args.get("resultFilePath", "")).strip() or None,
        result_format=str(args.get("resultFormat", "json")),
        inline_limit_bytes=args.get("inlineLimitBytes", runtime_snippets.DEFAULT_INLINE_LIMIT_BYTES),
    )


def _mw():
    from aqt import mw

    return mw


def _note_store() -> typed_tools.NoteStore:
    store = typed_tools.collection_store_from_anki()
    if store is None:
        raise RuntimeError("anki_collection_unavailable")
    return store


def _bridge_registry() -> dict[str, object]:
    payload = anki_bridge_capability_payload()
    profile = context_tools.current_profile(_mw())
    profile_name = str(profile.get("name") or "anki-local-profile")
    collection_base = str(profile.get("base") or "")
    return {
        "bridgeId": "anki-local-profile",
        "protocolVersion": "deckhand.ankiBridge.v1",
        "addonVersion": ADDON_VERSION,
        "profileHash": hashlib.sha256(profile_name.encode("utf-8")).hexdigest()[:16],
        "collectionHash": hashlib.sha256(collection_base.encode("utf-8")).hexdigest()[:16] if collection_base else None,
        "status": bridge_status.to_dict(),
        "capabilities": payload,
        "tools": payload["tools"],
    }


def show_onboarding() -> None:
    try:
        from aqt import mw
    except Exception as exc:  # pragma: no cover - only meaningful inside Anki/Qt
        _log("onboarding.unavailable", error=str(exc))
        return
    welcome.show_onboarding(mw, open_setup=show_management, logger=_log)


def show_management(initial_client: str | None = None) -> None:
    if not isinstance(initial_client, str):
        initial_client = None
    try:
        from aqt import mw
    except Exception as exc:  # pragma: no cover - only meaningful inside Anki/Qt
        _log("management.unavailable", error=str(exc))
        return
    management.show_management_dialog(mw, _executor.tools(), logger=_log, initial_client=initial_client)
