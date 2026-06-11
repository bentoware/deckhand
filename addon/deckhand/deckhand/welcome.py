"""First-run onboarding wizard for fresh Deckhand installs.

The wizard walks new users through the whole setup instead of pointing them
at the management dialog: welcome, pick an MCP host, follow that host's
guided steps, then verify the connection. The same recipes power the
management dialog's Connect tab, so the two never drift apart.
"""

from __future__ import annotations

import os
from typing import Any, Callable

from . import companion
from . import connect_hosts
from . import management
from . import settings
from . import ui
from .version import ADDON_VERSION

WELCOME_TITLE = "Welcome to Deckhand"
WELCOME_TAGLINE = "Your AI assistant's hands inside Anki."
WELCOME_BODY = (
    "Deckhand lets AI assistants like Claude and Codex read and operate your Anki "
    "collection through a local MCP server. Everything runs on your computer."
)
WIZARD_PAGE_TITLES = [
    "Welcome",
    "Choose your app",
    "Connect your app",
    "Check and finish",
]
WIZARD_PLAN = [
    ("Pick your AI app", "Claude, Codex, or any other MCP-capable assistant."),
    ("Connect it to Anki", "Install a small plugin or extension, or paste a command — no accounts, no cloud."),
    ("Check and study", "Run a connection check, then ask your assistant about your decks."),
]
WELCOME_SKIP_ACTION = "Skip for now"
WELCOME_PANEL_ACTION = "Open Deckhand panel"


def should_show() -> bool:
    if os.environ.get("DECKHAND_WELCOME_DISABLED") == "1":
        return False
    return not settings.welcome_shown()


def maybe_show_welcome(mw: Any, open_setup: Callable[..., None] | None = None, logger=None) -> bool:
    """Show the first-run wizard once. Returns True when it was shown."""
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


def show_onboarding(mw: Any, open_setup: Callable[..., None] | None = None, logger=None) -> None:
    """Show the onboarding wizard on demand (Deckhand menu), regardless of first-run state."""
    settings.set_welcome_shown(True)
    _show_welcome_dialog(mw, open_setup, logger=logger)


def _show_welcome_dialog(mw: Any, open_setup: Callable[..., None] | None, logger=None) -> None:
    try:
        from aqt.qt import (
            QDialog,
            QFrame,
            QHBoxLayout,
            QLabel,
            QPlainTextEdit,
            QPushButton,
            QScrollArea,
            QStackedWidget,
            Qt,
            QVBoxLayout,
            QWidget,
        )
    except Exception as exc:  # pragma: no cover - only meaningful inside Anki/Qt
        if logger:
            logger("welcome.dialog_unavailable", error=str(exc))
        return

    pal = ui.palette()
    dialog = QDialog(mw)
    dialog.setWindowTitle("Deckhand")
    dialog.setModal(True)
    dialog.resize(720, 600)

    layout = QVBoxLayout(dialog)
    layout.setContentsMargins(32, 24, 32, 20)
    layout.setSpacing(14)

    selected_client = {"id": connect_hosts.CLIENT_CODEX_DESKTOP}
    mcp_url = f"{companion.companion_url().rstrip('/')}/mcp"
    token = settings.persistent_token() if settings.require_mcp_token() else None

    progress = QLabel("")
    progress.setTextFormat(Qt.TextFormat.RichText)
    layout.addWidget(progress)

    stack = QStackedWidget(dialog)
    layout.addWidget(stack, 1)

    # Page 1: welcome and what to expect.
    intro_page = QWidget()
    intro_layout = QVBoxLayout(intro_page)
    intro_layout.setContentsMargins(0, 0, 0, 0)
    intro_layout.setSpacing(10)
    intro_layout.addWidget(ui.title_label(WELCOME_TITLE, size=26))
    intro_layout.addWidget(ui.muted_label(WELCOME_TAGLINE, size=15))
    body = QLabel(WELCOME_BODY)
    body.setWordWrap(True)
    intro_layout.addWidget(body)

    plan_card = ui.section_frame()
    plan_layout = QVBoxLayout(plan_card)
    plan_layout.setContentsMargins(16, 14, 16, 14)
    plan_layout.setSpacing(10)
    plan_layout.addWidget(ui.section_title("Setup takes about two minutes"))
    for number, (plan_title, plan_detail) in enumerate(WIZARD_PLAN, start=1):
        plan_row = QHBoxLayout()
        plan_row.setSpacing(10)
        plan_row.addWidget(ui.status_pill(str(number), "neutral"), 0, Qt.AlignmentFlag.AlignTop)
        plan_text = QLabel(f"<b>{plan_title}</b><br>{plan_detail}")
        plan_text.setWordWrap(True)
        plan_row.addWidget(plan_text, 1)
        plan_layout.addLayout(plan_row)
    intro_layout.addWidget(plan_card)

    privacy_row = QHBoxLayout()
    privacy_row.setSpacing(8)
    privacy_row.addWidget(ui.status_pill("Private", "ok"))
    privacy_row.addWidget(
        ui.muted_label("Deckhand only listens on this computer and never uploads your collection."), 1
    )
    intro_layout.addLayout(privacy_row)
    intro_layout.addStretch(1)
    intro_layout.addWidget(ui.muted_label(f"Version {ADDON_VERSION}", size=12))
    stack.addWidget(intro_page)

    # Page 2: choose the MCP host.
    choose_page = QWidget()
    choose_layout = QVBoxLayout(choose_page)
    choose_layout.setContentsMargins(0, 0, 0, 0)
    choose_layout.setSpacing(10)
    choose_layout.addWidget(ui.title_label("Choose your app", size=20))
    choose_layout.addWidget(
        ui.muted_label(
            "Pick the app where Anki tools should appear. You can connect more apps later "
            "from the Deckhand panel."
        )
    )

    host_cards: dict[str, Any] = {}

    def select_host(client_id: str) -> None:
        selected_client["id"] = connect_hosts.normalize_client_id(client_id)
        for host_id, host_card in host_cards.items():
            host_card.setChecked(host_id == selected_client["id"])
        if logger:
            logger("welcome.host_selected", client=selected_client["id"])

    for host in connect_hosts.connect_hosts():
        card = _host_card(choose_page, host, select_host)
        host_cards[str(host["id"])] = card
        choose_layout.addWidget(card)
    choose_layout.addStretch(1)
    stack.addWidget(choose_page)

    # Page 3: the guided setup for the chosen host.
    connect_page = QWidget()
    connect_layout = QVBoxLayout(connect_page)
    connect_layout.setContentsMargins(0, 0, 0, 0)
    connect_layout.setSpacing(8)
    recipe_view = ui.build_recipe_view(connect_page, logger=logger, log_prefix="welcome")
    recipe_scroll = QScrollArea(connect_page)
    recipe_scroll.setWidgetResizable(True)
    recipe_scroll.setFrameShape(QFrame.Shape.NoFrame)
    recipe_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    recipe_scroll.setWidget(recipe_view["widget"])
    connect_layout.addWidget(recipe_scroll, 1)
    stack.addWidget(connect_page)

    # Page 4: verify the plumbing and finish.
    finish_page = QWidget()
    finish_layout = QVBoxLayout(finish_page)
    finish_layout.setContentsMargins(0, 0, 0, 0)
    finish_layout.setSpacing(10)
    finish_layout.addWidget(ui.title_label("Check and finish", size=20))
    verify_hint = QLabel("")
    verify_hint.setWordWrap(True)
    finish_layout.addWidget(verify_hint)

    check_card = ui.section_frame()
    check_layout = QVBoxLayout(check_card)
    check_layout.setContentsMargins(16, 14, 16, 14)
    check_layout.setSpacing(8)
    check_layout.addWidget(ui.section_title("Connection check"))
    test_button = QPushButton("Test connection")
    check_results = QPlainTextEdit()
    check_results.setReadOnly(True)
    check_results.setPlaceholderText(
        'Click "Test connection" to confirm every link between Deckhand and Anki is working.'
    )

    def run_checks() -> None:
        checks = management.run_connection_checks()
        check_results.setPlainText(management.format_connection_checks(checks))
        if logger:
            logger(
                "welcome.connection_test_run",
                passed=all(check["ok"] for check in checks if not check.get("optional")),
            )

    test_button.clicked.connect(lambda _checked=False: run_checks())
    test_row = QHBoxLayout()
    test_row.addWidget(test_button)
    test_row.addStretch(1)
    check_layout.addLayout(test_row)
    check_layout.addWidget(check_results, 1)
    finish_layout.addWidget(check_card, 1)

    skills_row = QHBoxLayout()
    skills_row.setSpacing(8)
    skills_row.addWidget(ui.status_pill("Tip", "neutral"))
    skills_row.addWidget(
        ui.muted_label(
            "Install Deckhand's bundled study skills from the Skills tab of the Deckhand "
            "panel to teach your assistant proven Anki workflows."
        ),
        1,
    )
    finish_layout.addLayout(skills_row)
    stack.addWidget(finish_page)

    footer = QHBoxLayout()
    footer.setSpacing(8)
    skip_button = QPushButton(WELCOME_SKIP_ACTION)
    panel_button = QPushButton(WELCOME_PANEL_ACTION)
    back_button = QPushButton("Back")
    next_button = QPushButton("Continue")
    next_button.setDefault(True)
    footer.addWidget(skip_button)
    footer.addWidget(panel_button)
    footer.addStretch(1)
    footer.addWidget(back_button)
    footer.addWidget(next_button)
    layout.addLayout(footer)

    def update_chrome() -> None:
        index = stack.currentIndex()
        count = stack.count()
        filled = " ".join("●" for _ in range(index + 1))
        empty = " ".join("○" for _ in range(count - index - 1))
        progress.setText(
            f'<span style="color: {pal["accent_border"]};">{filled}</span> '
            f'<span style="color: {pal["border"]};">{empty}</span>'
            f'&nbsp;&nbsp;<span style="color: {pal["muted"]};">'
            f"Step {index + 1} of {count} — {WIZARD_PAGE_TITLES[index]}</span>"
        )
        back_button.setVisible(index > 0)
        skip_button.setVisible(index < count - 1)
        panel_button.setVisible(index == count - 1)
        next_button.setText("Continue" if index < count - 1 else "Done")

    def go_to(index: int) -> None:
        if index == 2:
            recipe_view["render"](connect_hosts.connect_recipe(selected_client["id"], mcp_url, token))
        if index == 3:
            label = connect_hosts.host_label(selected_client["id"])
            verify_hint.setText(
                f'Once {label} has restarted, ask it: "Can you list my Anki decks?" '
                "You can also confirm everything on this side right now."
            )
        stack.setCurrentIndex(index)
        update_chrome()
        if logger:
            logger("welcome.page_viewed", page=WIZARD_PAGE_TITLES[index], client=selected_client["id"])

    def go_next() -> None:
        index = stack.currentIndex()
        if index >= stack.count() - 1:
            dialog.accept()
            if logger:
                logger("welcome.completed", client=selected_client["id"])
            return
        go_to(index + 1)

    def skip() -> None:
        dialog.reject()
        if logger:
            logger("welcome.skipped", page=WIZARD_PAGE_TITLES[stack.currentIndex()])

    def open_panel() -> None:
        dialog.accept()
        if open_setup is not None:
            open_setup(selected_client["id"])
        if logger:
            logger("welcome.setup_opened", client=selected_client["id"])

    next_button.clicked.connect(lambda _checked=False: go_next())
    back_button.clicked.connect(lambda _checked=False: go_to(max(0, stack.currentIndex() - 1)))
    skip_button.clicked.connect(lambda _checked=False: skip())
    panel_button.clicked.connect(lambda _checked=False: open_panel())

    host_cards[selected_client["id"]].setChecked(True)
    update_chrome()

    dialog.show()
    dialog.raise_()
    dialog.activateWindow()
    if logger:
        logger("welcome.shown")


def _host_card(parent: Any, host: dict[str, Any], on_select: Callable[[str], None]) -> Any:
    """Selectable card for one MCP host: name, tagline, and difficulty chip."""
    from aqt.qt import QHBoxLayout, QLabel, QPushButton, Qt, QVBoxLayout

    pal = ui.palette()
    card = QPushButton(parent)
    card.setCheckable(True)
    card.setCursor(Qt.CursorShape.PointingHandCursor)
    card.setStyleSheet(
        f"QPushButton {{ border: 1px solid {pal['border']}; border-radius: 10px; "
        f"background: {pal['surface']}; text-align: left; }}"
        f"QPushButton:checked {{ background: {pal['accent_bg']}; "
        f"border: 1px solid {pal['accent_border']}; }}"
    )

    inner = QHBoxLayout(card)
    inner.setContentsMargins(14, 10, 14, 10)
    inner.setSpacing(12)

    text_column = QVBoxLayout()
    text_column.setSpacing(2)
    name = QLabel(str(host["label"]))
    name.setStyleSheet("font-size: 14px; font-weight: 700; background: transparent; border: none;")
    text_column.addWidget(name)
    tagline = QLabel(str(host["tagline"]))
    tagline.setWordWrap(True)
    tagline.setStyleSheet(f"color: {pal['muted']}; background: transparent; border: none;")
    text_column.addWidget(tagline)
    inner.addLayout(text_column, 1)

    difficulty = ui.status_pill(str(host["difficulty"]), "neutral")
    inner.addWidget(difficulty, 0, Qt.AlignmentFlag.AlignTop)

    # The labels are decoration; clicks must land on the checkable button.
    for child in (name, tagline, difficulty):
        child.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
    card.setMinimumHeight(inner.sizeHint().height())

    card.clicked.connect(lambda _checked=False: on_select(str(host["id"])))
    return card
