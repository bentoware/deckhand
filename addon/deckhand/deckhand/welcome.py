"""First-run welcome splash for fresh Deckhand installs."""

from __future__ import annotations

import os
from typing import Any, Callable

from . import settings
from .version import ADDON_VERSION

WELCOME_TITLE = "Welcome to Deckhand"
WELCOME_TAGLINE = "Your AI assistant's hands inside Anki."
WELCOME_BODY = (
    "Deckhand lets AI assistants like Claude and Codex read and operate your Anki "
    "collection through a local MCP server. Everything runs on your computer."
)
WELCOME_STEPS = [
    "Deckhand's local helper is already running alongside Anki.",
    "Connect your assistant: pick your client on the Connect tab and copy one snippet.",
    "Optional: install Deckhand's study skills from the Skills tab.",
]
WELCOME_PRIMARY_ACTION = "Open setup"
WELCOME_DISMISS_ACTION = "Maybe later"


def should_show() -> bool:
    if os.environ.get("DECKHAND_WELCOME_DISABLED") == "1":
        return False
    return not settings.welcome_shown()


def maybe_show_welcome(mw: Any, open_setup: Callable[[], None] | None = None, logger=None) -> bool:
    """Show the first-run splash once. Returns True when it was shown."""
    if not should_show():
        return False
    settings.set_welcome_shown(True)
    try:
        from aqt.qt import QTimer
    except Exception as exc:  # pragma: no cover - only meaningful inside Anki/Qt
        if logger:
            logger("welcome.unavailable", error=str(exc))
        return False
    # Let Anki finish painting its main window before putting up a dialog.
    QTimer.singleShot(800, lambda: _show_welcome_dialog(mw, open_setup, logger=logger))
    if logger:
        logger("welcome.scheduled")
    return True


def show_onboarding(mw: Any, open_setup: Callable[[], None] | None = None, logger=None) -> None:
    """Show the onboarding dialog on demand (Deckhand menu), regardless of first-run state."""
    settings.set_welcome_shown(True)
    _show_welcome_dialog(mw, open_setup, logger=logger)


def _show_welcome_dialog(mw: Any, open_setup: Callable[[], None] | None, logger=None) -> None:
    try:
        from aqt.qt import QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout
    except Exception as exc:  # pragma: no cover - only meaningful inside Anki/Qt
        if logger:
            logger("welcome.dialog_unavailable", error=str(exc))
        return

    dialog = QDialog(mw)
    dialog.setWindowTitle("Deckhand")
    dialog.setModal(True)
    dialog.resize(520, 360)
    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(32, 28, 32, 24)
    layout.setSpacing(12)

    heading = QLabel(WELCOME_TITLE)
    heading.setStyleSheet("font-size: 26px; font-weight: 700;")
    layout.addWidget(heading)

    tagline = QLabel(WELCOME_TAGLINE)
    tagline.setStyleSheet("font-size: 15px; color: #666;")
    layout.addWidget(tagline)

    body = QLabel(WELCOME_BODY)
    body.setWordWrap(True)
    layout.addWidget(body)

    for number, step in enumerate(WELCOME_STEPS, start=1):
        step_label = QLabel(f"{number}.  {step}")
        step_label.setWordWrap(True)
        layout.addWidget(step_label)

    version_label = QLabel(f"Version {ADDON_VERSION}")
    version_label.setStyleSheet("color: #999; font-size: 12px;")
    layout.addWidget(version_label)
    layout.addStretch(1)

    buttons = QHBoxLayout()
    buttons.addStretch(1)
    later = QPushButton(WELCOME_DISMISS_ACTION)
    later.clicked.connect(lambda _checked=False: dialog.reject())
    buttons.addWidget(later)
    setup_button = QPushButton(WELCOME_PRIMARY_ACTION)
    setup_button.setDefault(True)

    def launch_setup() -> None:
        dialog.accept()
        if open_setup is not None:
            open_setup()
        if logger:
            logger("welcome.setup_opened")

    setup_button.clicked.connect(lambda _checked=False: launch_setup())
    buttons.addWidget(setup_button)
    layout.addLayout(buttons)

    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    if logger:
        logger("welcome.shown")
