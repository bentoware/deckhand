"""Small Anki WebEngine SDK for use from ``anki_run_python``.

Import this module inside Deckhand code mode when you need rendered Anki UI data:

    import inspect
    import deckhand.web as web
    result = {"doc": inspect.getdoc(web), "members": [x for x in dir(web) if not x.startswith("_")]}

Typical usage:

    p = web.page()
    result = p.text()
    result = p.snapshot()
    result = p.html(file="/tmp/anki-main.html")
    result = p.screenshot("/tmp/anki-main.png")

Large values should be written with method-level ``file=`` arguments or with
``anki_run_python``'s ``resultFilePath`` parameter so the MCP response stays small.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Any, Callable, TypeVar

from . import webengine_tools

T = TypeVar("T")


def page(preferred: str = "main", **target: Any) -> "Page":
    """Return a WebEngine page handle.

    ``preferred`` may be ``"main"``, ``"first"``, or ``"strict"``. Pass target
    selectors such as ``title=``, ``urlContains=``, ``pageId=``,
    ``webSocketDebuggerUrl=``, ``host=``, ``port=``, or ``timeoutSeconds=`` when
    the intended WebEngine page is ambiguous.
    """
    return Page(preferred=preferred, target=target)


def pages(host: str | None = None, port: int | None = None, timeout_seconds: float = 2.0) -> dict[str, object]:
    """List debuggable Anki Qt WebEngine pages from the local CDP endpoint."""
    return _run_cdp(lambda: webengine_tools.list_pages(host=host, port=port, timeout=timeout_seconds))


def status(host: str | None = None, port: int | None = None, timeout_seconds: float = 2.0) -> dict[str, object]:
    """Check whether Anki's local Qt WebEngine CDP endpoint is reachable."""
    return _run_cdp(lambda: webengine_tools.status(host=host, port=port, timeout=timeout_seconds))


@dataclass(frozen=True)
class Page:
    """A handle for one Anki Qt WebEngine page."""

    preferred: str = "main"
    target: dict[str, Any] | None = None

    def text(self, max_chars: int | None = None) -> str:
        """Return visible document text. ``max_chars`` is an explicit caller cap."""
        script = "document.body ? document.body.innerText : ''"
        text = self.eval(script)
        text = str(text or "")
        if max_chars is None:
            return text
        return text[: max(0, int(max_chars))]

    def snapshot(self, max_elements: int = 200, max_tree_nodes: int = 250, *, verbose: bool = False, file: str | None = None) -> dict[str, object]:
        """Return a compact accessibility-ish page snapshot with stable uid refs."""
        args = {
            **self._target_args(),
            "maxElements": max_elements,
            "maxTreeNodes": max_tree_nodes,
            "verbose": verbose,
        }
        if file:
            args["filePath"] = file
        return _run_cdp(lambda: webengine_tools.take_snapshot(args))

    def html(self, file: str | None = None) -> str | dict[str, object]:
        """Return full document HTML, or write it to ``file`` and return artifact metadata."""
        html = str(self.eval("document.documentElement ? document.documentElement.outerHTML : ''") or "")
        if not file:
            return html
        path = Path(file).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(html, encoding="utf-8")
        return {"path": str(path), "bytes": path.stat().st_size, "format": "html"}

    def screenshot(self, file: str, format: str = "png", **options: Any) -> dict[str, object]:
        """Capture a screenshot to ``file`` and return artifact metadata."""
        args = {
            **self._target_args(),
            "filePath": file,
            "format": format,
            **options,
        }
        return _run_cdp(lambda: webengine_tools.take_screenshot(args))

    def eval(self, script: str) -> Any:
        """Evaluate JavaScript in the page and return the raw JSON-by-value result."""
        return _run_cdp(lambda: self._eval_cdp(script))

    def click(self, **selector: Any) -> dict[str, object]:
        """Click by ``uid``, ``selector``, ``text``, or ``x``/``y`` coordinates."""
        args = {**self._target_args(), **selector}
        return _run_cdp(lambda: webengine_tools.click(args))

    def type(self, text: str, **selector: Any) -> dict[str, object]:
        """Type text, optionally focusing by ``uid`` or ``selector`` first."""
        args = {**self._target_args(), "text": text, **selector}
        return _run_cdp(lambda: webengine_tools.type_text(args))

    def press(self, key: str, **options: Any) -> dict[str, object]:
        """Dispatch a key press such as ``Enter``, ``Escape``, or ``ArrowDown``."""
        args = {**self._target_args(), "key": key, **options}
        return _run_cdp(lambda: webengine_tools.press_key(args))

    def wait_for(self, **condition: Any) -> dict[str, object]:
        """Wait for a selector, text, URL/title substring, or JavaScript expression."""
        args = {**self._target_args(), **condition}
        return _run_cdp(lambda: webengine_tools.wait_for(args))

    def _target_args(self) -> dict[str, Any]:
        return {"preferredTarget": self.preferred, **(self.target or {})}

    def _eval_cdp(self, script: str) -> Any:
        target_args = self._target_args()
        target = webengine_tools._resolve_target(target_args)  # noqa: SLF001 - internal SDK wrapper
        response = webengine_tools._cdp_request(  # noqa: SLF001 - internal SDK wrapper
            target.websocket_url,
            "Runtime.evaluate",
            {"expression": script, "returnByValue": True, "awaitPromise": True},
            timeout=float(target_args.get("timeoutSeconds", 5.0)),
        )
        return webengine_tools._runtime_value(response)  # noqa: SLF001 - internal SDK wrapper


def _run_cdp(operation: Callable[[], T]) -> T:
    app, on_main_thread = _qt_app_state()
    if app is None or not on_main_thread:
        return operation()

    done = threading.Event()
    box: dict[str, Any] = {}

    def worker() -> None:
        try:
            box["value"] = operation()
        except BaseException as exc:  # noqa: BLE001 - re-raise on caller thread below
            box["error"] = exc
        finally:
            done.set()

    threading.Thread(target=worker, name="deckhand-web-cdp", daemon=True).start()
    while not done.wait(0.01):
        app.processEvents()
    if "error" in box:
        raise box["error"]
    return box.get("value")


def _qt_app_state() -> tuple[Any | None, bool]:
    try:
        from aqt.qt import QApplication, QThread
    except Exception:
        return None, False
    app = QApplication.instance()
    if app is None:
        return None, False
    return app, app.thread() == QThread.currentThread()
