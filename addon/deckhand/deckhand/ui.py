"""Theme-aware Qt styling helpers shared by Deckhand's dialogs.

Anki ships both a light and a night theme, so every color Deckhand paints
comes from the palette below instead of hardcoded hex values sprinkled
through dialog code. Styles are scoped with object names because QLabel
subclasses QFrame: a bare ``QFrame { border: ... }`` selector on a card
draws a border around every label inside it.

Qt imports stay inside functions so the module imports cleanly in plain
test environments without aqt installed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from . import connect_hosts
from . import mcpb

_LIGHT = {
    "border": "#d5d9de",
    "card": "#f8f9fa",
    "surface": "#ffffff",
    "muted": "#68707a",
    "mono": "#f3f5f7",
    "ok_fg": "#0f5132",
    "ok_bg": "#d1e7dd",
    "warn_fg": "#664d03",
    "warn_bg": "#fff3cd",
    "neutral_fg": "#343a40",
    "neutral_bg": "#e9ecef",
    "accent_fg": "#073b2a",
    "accent_bg": "#d7f4e5",
    "accent_border": "#68c692",
}

_DARK = {
    "border": "#46494e",
    "card": "#2b2d31",
    "surface": "#35383d",
    "muted": "#9aa1a9",
    "mono": "#26282c",
    "ok_fg": "#9be3c0",
    "ok_bg": "#1d4032",
    "warn_fg": "#ffd97a",
    "warn_bg": "#4a3a12",
    "neutral_fg": "#d0d4d9",
    "neutral_bg": "#3f444b",
    "accent_fg": "#bfeed6",
    "accent_bg": "#1d4634",
    "accent_border": "#3f9d6d",
}


def is_night_mode() -> bool:
    try:
        from aqt.theme import theme_manager

        return bool(theme_manager.night_mode)
    except Exception:
        return False


def palette() -> dict[str, str]:
    return _DARK if is_night_mode() else _LIGHT


def title_label(text: str, *, size: int = 24) -> Any:
    from aqt.qt import QLabel

    label = QLabel(text)
    label.setStyleSheet(f"font-size: {size}px; font-weight: 700;")
    return label


def muted_label(text: str, *, size: int | None = None) -> Any:
    from aqt.qt import QLabel

    label = QLabel(text)
    label.setWordWrap(True)
    size_rule = f" font-size: {size}px;" if size else ""
    label.setStyleSheet(f"color: {palette()['muted']};{size_rule}")
    return label


def section_title(text: str) -> Any:
    from aqt.qt import QLabel

    label = QLabel(text)
    label.setStyleSheet("font-weight: 700;")
    return label


def section_frame() -> Any:
    from aqt.qt import QFrame

    pal = palette()
    frame = QFrame()
    frame.setObjectName("deckhand_section")
    frame.setStyleSheet(
        f"#deckhand_section {{ border: 1px solid {pal['border']}; "
        f"border-radius: 10px; background: {pal['card']}; }}"
    )
    return frame


def status_pill(text: str, state: str) -> Any:
    from aqt.qt import QLabel

    label = QLabel(text)
    apply_pill_style(label, state)
    return label


def apply_pill_style(label: Any, state: str) -> None:
    pal = palette()
    colors = {
        "ok": (pal["ok_fg"], pal["ok_bg"]),
        "warn": (pal["warn_fg"], pal["warn_bg"]),
    }
    fg, bg = colors.get(state, (pal["neutral_fg"], pal["neutral_bg"]))
    label.setStyleSheet(
        f"color: {fg}; background: {bg}; border-radius: 10px; padding: 3px 9px; font-weight: 600;"
    )


def host_pill_style() -> str:
    pal = palette()
    # Weight stays constant between states: bolding only the checked pill
    # widens its text past the width Qt computed and clips the label.
    return (
        f"QPushButton {{ border: 1px solid {pal['border']}; border-radius: 13px; "
        f"padding: 6px 12px; background: {pal['surface']}; font-weight: 600; }}"
        f"QPushButton:checked {{ color: {pal['accent_fg']}; background: {pal['accent_bg']}; "
        f"border-color: {pal['accent_border']}; }}"
    )


def build_host_pillbar(parent: Any, on_select: Callable[[str], None]) -> dict[str, Any]:
    """One-row pill chooser over the shared MCP host list.

    Returns ``{"widget", "buttons", "set_selected"}``; selection styling is
    driven by ``set_selected`` so callers stay the single source of truth.
    """
    from aqt.qt import QButtonGroup, QHBoxLayout, QPushButton, Qt, QWidget

    widget = QWidget(parent)
    row = QHBoxLayout(widget)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(6)
    group = QButtonGroup(widget)
    group.setExclusive(True)
    buttons: dict[str, Any] = {}
    style = host_pill_style()
    for host in connect_hosts.connect_hosts():
        button = QPushButton(str(host["label"]))
        button.setCheckable(True)
        button.setProperty("clientId", host["id"])
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(style)
        group.addButton(button)
        buttons[str(host["id"])] = button
        row.addWidget(button)
    row.addStretch(1)

    def set_selected(client_id: str) -> None:
        for host_id, button in buttons.items():
            button.setChecked(host_id == client_id)

    group.buttonClicked.connect(lambda button: on_select(str(button.property("clientId"))))
    return {"widget": widget, "buttons": buttons, "set_selected": set_selected}


def build_recipe_view(parent: Any, *, logger=None, log_prefix: str = "ui") -> dict[str, Any]:
    """Card that renders a connect-host recipe: steps, snippet, and extras.

    Returns ``{"widget", "render"}`` where ``render(recipe)`` repaints the
    card for a recipe from ``connect_hosts.connect_recipe``.
    """
    from aqt.qt import (
        QFrame,
        QGuiApplication,
        QHBoxLayout,
        QLabel,
        QPlainTextEdit,
        QPushButton,
        Qt,
        QVBoxLayout,
        QWidget,
    )

    pal = palette()
    card = section_frame()
    card.setParent(parent)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(16, 14, 16, 14)
    layout.setSpacing(10)

    title = QLabel("")
    title.setStyleSheet("font-size: 17px; font-weight: 700;")
    layout.addWidget(title)

    intro = QLabel("")
    intro.setWordWrap(True)
    intro.setStyleSheet(f"color: {pal['muted']};")
    layout.addWidget(intro)

    steps_box = QWidget(card)
    steps_layout = QVBoxLayout(steps_box)
    steps_layout.setContentsMargins(0, 0, 0, 0)
    steps_layout.setSpacing(6)
    layout.addWidget(steps_box)

    snippet_title = section_title("")
    layout.addWidget(snippet_title)
    snippet = QPlainTextEdit()
    snippet.setReadOnly(True)
    snippet.setMaximumHeight(96)
    snippet.setStyleSheet(
        f"QPlainTextEdit {{ background: {pal['mono']}; border: 1px solid {pal['border']}; "
        "border-radius: 6px; font-family: Menlo, Monaco, 'Courier New', monospace; }"
    )
    layout.addWidget(snippet)

    state = {"client": ""}
    copy_button = QPushButton("Copy")

    def copy_snippet() -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(snippet.toPlainText())
        if logger:
            logger(f"{log_prefix}.connect_snippet_copied", client=state["client"])

    copy_button.clicked.connect(lambda _checked=False: copy_snippet())
    copy_row = QHBoxLayout()
    copy_row.addStretch(1)
    copy_row.addWidget(copy_button)
    layout.addLayout(copy_row)

    # The drag-or-save chip renders inside the step that mentions it, so it
    # lives detached from the layout until render() embeds it.
    mcpb_section = make_mcpb_section(card, logger=logger, log_prefix=log_prefix)
    mcpb_section.hide()

    step_style = (
        f"#deckhand_step {{ border: 1px solid {pal['border']}; "
        f"border-radius: 8px; background: {pal['surface']}; }}"
    )

    def clear_steps() -> None:
        # Pull the embedded chip out before the step frames are deleted, or
        # it would be destroyed along with its host frame.
        mcpb_section.hide()
        mcpb_section.setParent(card)
        while steps_layout.count():
            item = steps_layout.takeAt(0)
            child = item.widget()
            if child is not None:
                child.deleteLater()

    def add_step(number: int, step_title: str, body: str, embed: Any = None) -> None:
        row = QFrame(steps_box)
        row.setObjectName("deckhand_step")
        row.setStyleSheet(step_style)
        row_layout = QHBoxLayout(row)
        row_layout.setContentsMargins(10, 8, 10, 8)
        row_layout.setSpacing(10)
        row_layout.addWidget(status_pill(str(number), "neutral"), 0, Qt.AlignmentFlag.AlignTop)
        text = QLabel(f"<b>{step_title}</b><br>{body}")
        text.setWordWrap(True)
        if embed is None:
            row_layout.addWidget(text, 1)
        else:
            column = QVBoxLayout()
            column.setSpacing(8)
            column.addWidget(text)
            column.addWidget(embed)
            embed.show()
            row_layout.addLayout(column, 1)
        steps_layout.addWidget(row)

    def render(recipe: dict[str, Any]) -> None:
        state["client"] = str(recipe["client"])
        title.setText(f"Set up {recipe['label']}")
        intro.setText(str(recipe["intro"]))
        clear_steps()
        for number, step in enumerate(recipe["steps"], start=1):
            embed = mcpb_section if step.get("embed") == "mcpb" else None
            add_step(number, str(step["title"]), str(step["body"]), embed=embed)
        snippet_title.setText(str(recipe["snippetLabel"]))
        snippet.setPlainText(str(recipe["snippet"]))
        action = str(recipe.get("primaryAction") or "")
        copy_button.setText(action if action.startswith("Copy") else "Copy")

    return {"widget": card, "render": render}


def make_mcpb_section(parent: Any, logger=None, log_prefix: str = "ui") -> Any:
    """Drag-or-save chip for the Claude Desktop extension bundle."""
    from aqt.qt import (
        QDrag,
        QFileDialog,
        QHBoxLayout,
        QLabel,
        QMimeData,
        QPushButton,
        Qt,
        QUrl,
        QWidget,
    )

    pal = palette()
    section = QWidget(parent)
    row = QHBoxLayout(section)
    row.setContentsMargins(0, 4, 0, 0)
    row.setSpacing(8)

    chip = QLabel("Install Deckhand in Claude Desktop — drag or save Deckhand.mcpb")
    # Without wrapping, the label's minimum width is the full text width,
    # which forces the recipe card past narrow viewports.
    chip.setWordWrap(True)
    chip.setStyleSheet(
        f"border: 2px dashed {pal['accent_border']}; border-radius: 10px; padding: 12px 14px; "
        f"font-weight: 600; background: {pal['accent_bg']}; color: {pal['accent_fg']};"
    )
    chip.setCursor(Qt.CursorShape.OpenHandCursor)

    def start_drag(event: Any) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        try:
            path = mcpb.build_bundle()
        except Exception as exc:  # noqa: BLE001 - surface build problems inline
            chip.setText(f"Could not build extension: {exc}")
            return
        drag = QDrag(chip)
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(path))])
        drag.setMimeData(mime)
        if logger:
            logger(f"{log_prefix}.mcpb_drag_started", path=str(path))
        drag.exec(Qt.DropAction.CopyAction)

    chip.mousePressEvent = start_drag
    row.addWidget(chip, 1)

    save_button = QPushButton("Save extension...")

    def save_bundle() -> None:
        suggested = str(Path.home() / "Downloads" / mcpb.BUNDLE_FILENAME)
        chosen, _selected_filter = QFileDialog.getSaveFileName(
            section, "Save Deckhand extension", suggested, "MCP Bundle (*.mcpb)"
        )
        if not chosen:
            return
        path = mcpb.build_bundle(Path(chosen))
        if logger:
            logger(f"{log_prefix}.mcpb_saved", path=str(path))

    save_button.clicked.connect(lambda _checked=False: save_bundle())
    row.addWidget(save_button)
    return section
