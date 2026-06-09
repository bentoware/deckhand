from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import socket
import struct
import time
from typing import Any, NamedTuple
from urllib.parse import urlparse
from urllib.request import urlopen

from .dev_tools import sanitize_result


DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 9222
MAX_HTTP_BYTES = 2_000_000
MAX_WS_BYTES = 8_000_000
ALLOWED_HOSTS = {"127.0.0.1", "localhost", "::1"}
MAX_SNAPSHOT_TEXT_CHARS = 12_000
MAX_SNAPSHOT_ELEMENTS = 200
MAX_SNAPSHOT_TREE_NODES = 250
KEY_DEFINITIONS = {
    "enter": ("Enter", "Enter", 13),
    "return": ("Enter", "Enter", 13),
    "tab": ("Tab", "Tab", 9),
    "escape": ("Escape", "Escape", 27),
    "esc": ("Escape", "Escape", 27),
    "backspace": ("Backspace", "Backspace", 8),
    "delete": ("Delete", "Delete", 46),
    "arrowup": ("ArrowUp", "ArrowUp", 38),
    "arrowdown": ("ArrowDown", "ArrowDown", 40),
    "arrowleft": ("ArrowLeft", "ArrowLeft", 37),
    "arrowright": ("ArrowRight", "ArrowRight", 39),
    "space": (" ", "Space", 32),
}


class WebEngineToolError(RuntimeError):
    pass


class TargetResolution(NamedTuple):
    websocket_url: str
    target: dict[str, object]


def status(host: str | None = None, port: int | None = None, timeout: float = 2.0) -> dict[str, object]:
    base = _base_url(host, port)
    try:
        version = _get_json(f"{base}/json/version", timeout=timeout)
    except Exception as exc:  # noqa: BLE001 - tool result should explain local runtime state
        return {
            "available": False,
            "host": _host(host),
            "port": _port(port),
            "url": f"{base}/json/version",
            "error": str(exc),
        }
    return {
        "available": True,
        "host": _host(host),
        "port": _port(port),
        "url": f"{base}/json/version",
        "version": sanitize_result(version),
    }


def list_pages(host: str | None = None, port: int | None = None, timeout: float = 2.0) -> dict[str, object]:
    pages = _list_pages(host, port, timeout)
    return {
        "host": _host(host),
        "port": _port(port),
        "pages": [_page_summary(page) for page in pages],
        "count": len(pages),
    }


def send_cdp_command(args: dict[str, Any]) -> dict[str, object]:
    method = str(args.get("method", "")).strip()
    params = args.get("params", {})
    if not method:
        raise WebEngineToolError("method_required")
    if not isinstance(params, dict):
        raise WebEngineToolError("params_must_be_object")

    preview = {
        "method": method,
        "paramsPreview": sanitize_result(params),
        "targetPreview": _target_preview(args),
    }
    target = _resolve_target(args)
    response = _cdp_request(target.websocket_url, method, params, timeout=float(args.get("timeoutSeconds", 5.0)))
    return {
        **preview,
        "webSocketDebuggerUrl": target.websocket_url,
        "target": target.target,
        "response": sanitize_result(response),
    }


def take_snapshot(args: dict[str, Any]) -> dict[str, object]:
    max_elements = _positive_int(args.get("maxElements"), MAX_SNAPSHOT_ELEMENTS)
    max_tree_nodes = _positive_int(args.get("maxTreeNodes"), MAX_SNAPSHOT_TREE_NODES)
    verbose = bool(args.get("verbose", False))
    target = _resolve_target(args)
    response = _cdp_request(
        target.websocket_url,
        "Runtime.evaluate",
        {
            "expression": _snapshot_expression(max_elements, max_tree_nodes, verbose),
            "returnByValue": True,
            "awaitPromise": True,
        },
        timeout=float(args.get("timeoutSeconds", 5.0)),
    )
    snapshot = sanitize_result(_runtime_value(response))
    file_path = str(args.get("filePath", "")).strip()
    if file_path:
        path = Path(file_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        text = snapshot.get("text", "") if isinstance(snapshot, dict) else ""
        path.write_text(str(text), encoding="utf-8")
        return {
            "webSocketDebuggerUrl": target.websocket_url,
            "target": target.target,
            "snapshotId": snapshot.get("snapshotId") if isinstance(snapshot, dict) else None,
            "path": str(path),
            "bytes": path.stat().st_size,
        }
    return {
        "webSocketDebuggerUrl": target.websocket_url,
        "target": target.target,
        "snapshot": snapshot,
    }


def take_screenshot(args: dict[str, Any]) -> dict[str, object]:
    file_path = str(args.get("filePath", "")).strip()
    if not file_path:
        raise WebEngineToolError("filePath_required")
    fmt = str(args.get("format", "png")).lower()
    if fmt not in {"png", "jpeg", "webp"}:
        raise WebEngineToolError("unsupported_screenshot_format")
    params: dict[str, Any] = {
        "format": fmt,
        "captureBeyondViewport": bool(args.get("captureBeyondViewport", False)),
    }
    if fmt in {"jpeg", "webp"} and args.get("quality") is not None:
        params["quality"] = max(0, min(100, int(args.get("quality"))))
    target = _resolve_target(args)
    response = _cdp_request(
        target.websocket_url,
        "Page.captureScreenshot",
        params,
        timeout=float(args.get("timeoutSeconds", 10.0)),
    )
    data = response.get("result", {}).get("data") if isinstance(response.get("result"), dict) else None
    if not isinstance(data, str) or not data:
        raise WebEngineToolError("screenshot_data_missing")
    raw = base64.b64decode(data)
    path = Path(file_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return {
        "webSocketDebuggerUrl": target.websocket_url,
        "target": target.target,
        "path": str(path),
        "mimeType": f"image/{fmt}",
        "bytes": len(raw),
        "format": fmt,
    }


def evaluate_script(args: dict[str, Any]) -> dict[str, object]:
    script = str(args.get("script", "")).strip()
    if not script:
        raise WebEngineToolError("script_required")
    target = _resolve_target(args)
    response = _cdp_request(
        target.websocket_url,
        "Runtime.evaluate",
        {
            "expression": script,
            "awaitPromise": bool(args.get("awaitPromise", True)),
            "returnByValue": bool(args.get("returnByValue", True)),
        },
        timeout=float(args.get("timeoutSeconds", 5.0)),
    )
    return {
        "webSocketDebuggerUrl": target.websocket_url,
        "target": target.target,
        "response": sanitize_result(response),
        "value": sanitize_result(_runtime_value(response)),
    }


def click(args: dict[str, Any]) -> dict[str, object]:
    x = args.get("x")
    y = args.get("y")
    target = _resolve_target(args)
    websocket_url = target.websocket_url
    timeout = float(args.get("timeoutSeconds", 5.0))
    if x is None or y is None:
        uid = str(args.get("uid", "")).strip()
        selector = str(args.get("selector", "")).strip()
        text = str(args.get("text", "")).strip()
        if not uid and not selector and not text:
            raise WebEngineToolError("click_target_required")
        resolved = _cdp_request(
            websocket_url,
            "Runtime.evaluate",
            {
                "expression": _element_center_expression(uid=uid, selector=selector, text=text),
                "returnByValue": True,
                "awaitPromise": True,
            },
            timeout=timeout,
        )
        point = _runtime_value(resolved)
        if not isinstance(point, dict) or point.get("ok") is not True:
            raise WebEngineToolError(str(point.get("error", "click_target_not_found")) if isinstance(point, dict) else "click_target_not_found")
        x = point.get("x")
        y = point.get("y")
    button = str(args.get("button", "left"))
    click_count = _positive_int(args.get("clickCount"), 1)
    for event_type in ("mousePressed", "mouseReleased"):
        _cdp_request(
            websocket_url,
            "Input.dispatchMouseEvent",
            {"type": event_type, "x": float(x), "y": float(y), "button": button, "clickCount": click_count},
            timeout=timeout,
        )
    return {
        "webSocketDebuggerUrl": websocket_url,
        "target": target.target,
        "x": float(x),
        "y": float(y),
        "button": button,
        "clickCount": click_count,
        "uid": str(args.get("uid", "")).strip() or None,
    }


def type_text(args: dict[str, Any]) -> dict[str, object]:
    text = str(args.get("text", ""))
    if text == "":
        raise WebEngineToolError("text_required")
    target = _resolve_target(args)
    websocket_url = target.websocket_url
    timeout = float(args.get("timeoutSeconds", 5.0))
    uid = str(args.get("uid", "")).strip()
    selector = str(args.get("selector", "")).strip()
    if uid or selector:
        response = _cdp_request(
            websocket_url,
            "Runtime.evaluate",
            {
                "expression": _focus_expression(uid=uid, selector=selector, clear=bool(args.get("clear", False))),
                "returnByValue": True,
                "awaitPromise": True,
            },
            timeout=timeout,
        )
        value = _runtime_value(response)
        if not isinstance(value, dict) or value.get("ok") is not True:
            raise WebEngineToolError(str(value.get("error", "selector_not_found")) if isinstance(value, dict) else "selector_not_found")
    _cdp_request(websocket_url, "Input.insertText", {"text": text}, timeout=timeout)
    return {
        "webSocketDebuggerUrl": websocket_url,
        "target": target.target,
        "textLength": len(text),
        "uid": uid or None,
        "selector": selector or None,
    }


def press_key(args: dict[str, Any]) -> dict[str, object]:
    raw_key = str(args.get("key", "")).strip()
    if not raw_key:
        raise WebEngineToolError("key_required")
    key, code, windows_key_code = _key_definition(raw_key, args)
    params = {
        "key": key,
        "code": code,
        "windowsVirtualKeyCode": windows_key_code,
        "nativeVirtualKeyCode": windows_key_code,
        "modifiers": int(args.get("modifiers", 0)),
    }
    target = _resolve_target(args)
    websocket_url = target.websocket_url
    timeout = float(args.get("timeoutSeconds", 5.0))
    _cdp_request(websocket_url, "Input.dispatchKeyEvent", {"type": "keyDown", **params}, timeout=timeout)
    _cdp_request(websocket_url, "Input.dispatchKeyEvent", {"type": "keyUp", **params}, timeout=timeout)
    return {
        "webSocketDebuggerUrl": websocket_url,
        "target": target.target,
        "key": key,
        "code": code,
    }


def wait_for(args: dict[str, Any]) -> dict[str, object]:
    timeout = float(args.get("timeoutSeconds", 5.0))
    poll_interval = max(0.05, float(args.get("pollIntervalSeconds", 0.1)))
    expression = _wait_expression(args)
    target = _resolve_target(args)
    websocket_url = target.websocket_url
    deadline = time.monotonic() + timeout
    last_value: Any = None
    while True:
        response = _cdp_request(
            websocket_url,
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
            timeout=max(0.1, min(timeout, 2.0)),
        )
        last_value = _runtime_value(response)
        if _truthy_wait_value(last_value):
            return {
                "webSocketDebuggerUrl": websocket_url,
                "target": target.target,
                "matched": True,
                "value": sanitize_result(last_value),
            }
        if time.monotonic() >= deadline:
            raise WebEngineToolError("wait_for_timeout")
        time.sleep(min(poll_interval, max(0.0, deadline - time.monotonic())))


def _runtime_value(response: dict[str, Any]) -> Any:
    result = response.get("result")
    if not isinstance(result, dict):
        return None
    exception = result.get("exceptionDetails")
    if exception:
        raise WebEngineToolError(f"runtime_exception: {sanitize_result(exception)}")
    value = result.get("result")
    if not isinstance(value, dict):
        return None
    if "value" in value:
        return value.get("value")
    if "description" in value:
        return value.get("description")
    return value


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _json_literal(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"))


def _snapshot_expression(max_elements: int, max_tree_nodes: int, verbose: bool) -> str:
    return f"""(() => {{
  const maxElements = {_json_literal(max_elements)};
  let remainingTreeNodes = {_json_literal(max_tree_nodes)};
  const verbose = {_json_literal(verbose)};
  const snapshotId = String((window.__deckhandSnapshotCounter = (window.__deckhandSnapshotCounter || 0) + 1));
  const uidFor = (index) => `e${{snapshotId}}_${{index}}`;
  const visible = (el) => {{
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
  }};
  const directText = (el) => Array.from(el.childNodes)
    .filter((node) => node.nodeType === Node.TEXT_NODE)
    .map((node) => node.textContent || "")
    .join(" ")
    .replace(/\\s+/g, " ")
    .trim();
  const label = (el) => (el.getAttribute("aria-label") || el.getAttribute("title") || el.getAttribute("alt") || el.getAttribute("placeholder") || (("value" in el) ? el.value : "") || directText(el) || "").trim();
  const role = (el) => {{
    const explicit = el.getAttribute("role");
    if (explicit) return explicit;
    const tag = el.tagName.toLowerCase();
    if (tag === "a" && el.hasAttribute("href")) return "link";
    if (tag === "button") return "button";
    if (tag === "textarea") return "textbox";
    if (tag === "select") return "combobox";
    if (tag === "img") return "image";
    if (/^h[1-6]$/.test(tag)) return "heading";
    if (tag === "ul" || tag === "ol") return "list";
    if (tag === "li") return "listitem";
    if (tag === "input") {{
      const type = (el.getAttribute("type") || "text").toLowerCase();
      if (type === "checkbox") return "checkbox";
      if (type === "radio") return "radio";
      if (["button", "submit", "reset"].includes(type)) return "button";
      return "textbox";
    }}
    if (el.isContentEditable) return "textbox";
    return "generic";
  }};
  const interactive = (el) => {{
    const tag = el.tagName.toLowerCase();
    return tag === "a" || tag === "button" || tag === "input" || tag === "textarea" || tag === "select" || el.isContentEditable || el.hasAttribute("role") || el.tabIndex >= 0;
  }};
  const interesting = (el) => {{
    if (!visible(el)) return false;
    if (interactive(el)) return true;
    const tag = el.tagName.toLowerCase();
    if (/^h[1-6]$/.test(tag) || tag === "img" || tag === "li") return true;
    return verbose && !!label(el);
  }};
  const selectorFor = (el) => {{
    if (el.id) return `#${{CSS.escape(el.id)}}`;
    const parts = [];
    let current = el;
    while (current && current.nodeType === Node.ELEMENT_NODE && current !== document.body) {{
      const parent = current.parentElement;
      if (!parent) break;
      const tag = current.tagName.toLowerCase();
      const siblings = Array.from(parent.children).filter((child) => child.tagName === current.tagName);
      const suffix = siblings.length > 1 ? `:nth-of-type(${{siblings.indexOf(current) + 1}})` : "";
      parts.unshift(`${{tag}}${{suffix}}`);
      current = parent;
    }}
    return parts.length ? `body > ${{parts.join(" > ")}}` : "body";
  }};
  const recordFor = (el, index) => {{
    const rect = el.getBoundingClientRect();
    const record = {{
      uid: uidFor(index),
      role: role(el),
      name: label(el).slice(0, 240) || null,
      tag: el.tagName.toLowerCase(),
      selector: selectorFor(el),
      bounds: {{ x: Math.round(rect.x), y: Math.round(rect.y), width: Math.round(rect.width), height: Math.round(rect.height) }},
    }};
    if (el.disabled || el.getAttribute("aria-disabled") === "true") record.disabled = true;
    if (document.activeElement === el) record.focused = true;
    if ("checked" in el) record.checked = !!el.checked;
    if (el.getAttribute("aria-expanded") !== null) record.expanded = el.getAttribute("aria-expanded") === "true";
    return record;
  }};
  const elements = Array.from(document.querySelectorAll("body *"))
    .filter(interesting)
    .slice(0, maxElements)
    .map((el, index) => recordFor(el, index));
  const selectorToElement = new Map(elements.map((item) => [item.selector, item]));
  const buildTree = (el) => {{
    if (!visible(el) || remainingTreeNodes <= 0) return null;
    const children = [];
    for (const child of Array.from(el.children)) {{
      const childNode = buildTree(child);
      if (childNode) children.push(childNode);
      if (remainingTreeNodes <= 0) break;
    }}
    const own = selectorToElement.get(selectorFor(el));
    const text = directText(el);
    if (!own && !children.length && !text) return null;
    remainingTreeNodes -= 1;
    const node = own ? {{ uid: own.uid, role: own.role, name: own.name }} : {{ role: role(el), name: text.slice(0, 240) || null }};
    if (children.length) node.children = children;
    return node;
  }};
  const formatNode = (node, depth = 0) => {{
    if (!node) return "";
    const attrs = [];
    if (node.uid) attrs.push(`uid=${{node.uid}}`);
    if (node.role) attrs.push(node.role);
    if (node.name) attrs.push(`"${{node.name}}"`);
    return `${{" ".repeat(depth * 2)}}${{attrs.join(" ")}}\\n` + (node.children || []).map((child) => formatNode(child, depth + 1)).join("");
  }};
  const root = buildTree(document.body) || {{ role: "document", name: document.title || null }};
  const text = formatNode(root);
  window.__deckhandSnapshot = {{
    snapshotId,
    elements: Object.fromEntries(elements.map((item) => [item.uid, item])),
  }};
  return {{
    snapshotId,
    title: document.title,
    url: location.href,
    text,
    root,
    elements: Object.fromEntries(elements.map((item) => [item.uid, item])),
    elementCount: elements.length,
    treeNodeCount: text.split("\\n").filter(Boolean).length,
    verbose,
  }};
}})()"""


def _element_center_expression(*, uid: str, selector: str, text: str) -> str:
    uid_json = _json_literal(uid)
    selector_json = _json_literal(selector)
    text_json = _json_literal(text.lower())
    return f"""(() => {{
  const uid = {uid_json};
  const selector = {selector_json};
  const text = {text_json};
  const visible = (el) => {{
    const style = getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    return style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
  }};
  let el = null;
  if (uid) {{
    const item = window.__deckhandSnapshot && window.__deckhandSnapshot.elements && window.__deckhandSnapshot.elements[uid];
    if (!item) return {{ ok: false, error: "snapshot_uid_not_found" }};
    el = item.selector ? document.querySelector(item.selector) : null;
  }}
  if (!el && selector) el = document.querySelector(selector);
  if (!el && text) {{
    el = Array.from(document.querySelectorAll('a,button,input,textarea,select,label,[role],[contenteditable="true"],body *'))
      .find((candidate) => visible(candidate) && (candidate.innerText || candidate.value || candidate.getAttribute("aria-label") || "").toLowerCase().includes(text));
  }}
  if (!el) return {{ ok: false, error: "element_not_found" }};
  el.scrollIntoView({{ block: "center", inline: "center" }});
  const rect = el.getBoundingClientRect();
  return {{ ok: true, x: rect.left + rect.width / 2, y: rect.top + rect.height / 2 }};
}})()"""


def _focus_expression(*, uid: str, selector: str, clear: bool) -> str:
    return f"""(() => {{
  const uid = {_json_literal(uid)};
  const selector = {_json_literal(selector)};
  let el = null;
  if (uid) {{
    const item = window.__deckhandSnapshot && window.__deckhandSnapshot.elements && window.__deckhandSnapshot.elements[uid];
    if (!item) return {{ ok: false, error: "snapshot_uid_not_found" }};
    el = item.selector ? document.querySelector(item.selector) : null;
  }}
  if (!el && selector) el = document.querySelector(selector);
  if (!el) return {{ ok: false, error: "selector_not_found" }};
  el.scrollIntoView({{ block: "center", inline: "center" }});
  el.focus();
  if ({_json_literal(clear)}) {{
    if ("value" in el) el.value = "";
    else el.textContent = "";
    el.dispatchEvent(new Event("input", {{ bubbles: true }}));
    el.dispatchEvent(new Event("change", {{ bubbles: true }}));
  }}
  return {{ ok: true }};
}})()"""


def _wait_expression(args: dict[str, Any]) -> str:
    expression = str(args.get("expression", "")).strip()
    if expression:
        return f"Boolean(({expression}))"
    selector = str(args.get("selector", "")).strip()
    text = str(args.get("text", "")).strip()
    title = str(args.get("title", "")).strip()
    url_contains = str(args.get("urlContains", "")).strip()
    checks = []
    if selector:
        checks.append(f"document.querySelector({_json_literal(selector)}) !== null")
    if text:
        checks.append(f"(document.body && document.body.innerText || '').includes({_json_literal(text)})")
    if title:
        checks.append(f"document.title.includes({_json_literal(title)})")
    if url_contains:
        checks.append(f"location.href.includes({_json_literal(url_contains)})")
    if not checks:
        raise WebEngineToolError("wait_condition_required")
    return f"Boolean({ ' || '.join(checks) })"


def _truthy_wait_value(value: Any) -> bool:
    if isinstance(value, dict) and "ok" in value:
        return bool(value.get("ok"))
    return bool(value)


def _key_definition(raw_key: str, args: dict[str, Any]) -> tuple[str, str, int]:
    lower = raw_key.lower()
    if lower in KEY_DEFINITIONS:
        key, code, windows_key_code = KEY_DEFINITIONS[lower]
    elif len(raw_key) == 1:
        key = raw_key
        code = str(args.get("code") or f"Key{raw_key.upper()}" if raw_key.isalpha() else raw_key)
        windows_key_code = ord(raw_key.upper())
    else:
        key = raw_key
        code = str(args.get("code") or raw_key)
        windows_key_code = int(args.get("windowsVirtualKeyCode", 0))
    if args.get("code"):
        code = str(args.get("code"))
    if args.get("windowsVirtualKeyCode") is not None:
        windows_key_code = int(args.get("windowsVirtualKeyCode"))
    return key, code, windows_key_code


def _target_preview(args: dict[str, Any]) -> dict[str, object]:
    return {
        "webSocketDebuggerUrl": args.get("webSocketDebuggerUrl"),
        "pageId": args.get("pageId"),
        "title": args.get("title"),
        "urlContains": args.get("urlContains"),
        "preferredTarget": args.get("preferredTarget"),
    }


def _resolve_target(args: dict[str, Any]) -> TargetResolution:
    direct = args.get("webSocketDebuggerUrl")
    if direct:
        websocket_url = _validate_ws_url(str(direct))
        return TargetResolution(
            websocket_url,
            {
                "id": None,
                "type": None,
                "title": None,
                "url": None,
                "webSocketDebuggerUrl": websocket_url,
                "selectionReason": "explicit_websocket_url",
            },
        )

    pages = _list_pages(args.get("host"), args.get("port"), float(args.get("timeoutSeconds", 2.0)))
    page_id = str(args.get("pageId", "")).strip()
    title = str(args.get("title", "")).strip()
    url_contains = str(args.get("urlContains", "")).strip()
    preferred_target = str(args.get("preferredTarget", "main")).strip().lower() or "main"
    if preferred_target not in {"main", "first", "strict"}:
        raise WebEngineToolError("unsupported_preferredTarget")

    selection_reason = "first_page"
    candidates = pages
    if page_id:
        candidates = [page for page in candidates if str(page.get("id", "")) == page_id]
        selection_reason = "matched_page_id"
    if title:
        candidates = [page for page in candidates if title.lower() in str(page.get("title", "")).lower()]
        selection_reason = "matched_title"
    if url_contains:
        candidates = [page for page in candidates if url_contains in str(page.get("url", ""))]
        selection_reason = "matched_url"
    if not candidates:
        raise WebEngineToolError("no_matching_webengine_page")
    if len(candidates) > 1 and not (page_id or title or url_contains):
        if preferred_target == "strict":
            raise WebEngineToolError("ambiguous_webengine_page")
        if preferred_target == "main":
            main = [page for page in candidates if str(page.get("title", "")).lower() == "main webview"]
            if main:
                candidates = main
                selection_reason = "preferred_main_webview"
            else:
                selection_reason = "first_page"
        else:
            selection_reason = "first_page"
    elif len(candidates) == 1 and preferred_target == "strict" and not (page_id or title or url_contains):
        selection_reason = "strict_single_match"
    page = candidates[0]
    websocket_url = page.get("webSocketDebuggerUrl")
    if not isinstance(websocket_url, str) or not websocket_url:
        raise WebEngineToolError("matching_page_has_no_websocket_url")
    websocket_url = _validate_ws_url(websocket_url)
    target = _page_summary(page)
    target["webSocketDebuggerUrl"] = websocket_url
    target["selectionReason"] = selection_reason
    return TargetResolution(websocket_url, target)


def _list_pages(host: str | None, port: int | None, timeout: float) -> list[dict[str, Any]]:
    data = _get_json(f"{_base_url(host, port)}/json/list", timeout=timeout)
    if not isinstance(data, list):
        raise WebEngineToolError("json_list_response_must_be_array")
    return [page for page in data if isinstance(page, dict)]


def _get_json(url: str, timeout: float) -> Any:
    parsed = urlparse(url)
    _validate_host(parsed.hostname)
    with urlopen(url, timeout=timeout) as response:  # noqa: S310 - restricted to local debug endpoint
        body = response.read(MAX_HTTP_BYTES + 1)
    if len(body) > MAX_HTTP_BYTES:
        raise WebEngineToolError("cdp_http_response_too_large")
    return json.loads(body.decode("utf-8"))


def _cdp_request(websocket_url: str, method: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
    with CdpSession(websocket_url, timeout=timeout) as session:
        return session.send(method, params)


class CdpSession:
    def __init__(self, websocket_url: str, *, timeout: float) -> None:
        self._websocket_url = _validate_ws_url(websocket_url)
        self._timeout = timeout
        self._sock: socket.socket | None = None
        self._next_id = 1

    def __enter__(self) -> "CdpSession":
        parsed = urlparse(self._websocket_url)
        host = parsed.hostname or DEFAULT_HOST
        port = parsed.port or DEFAULT_PORT
        path = parsed.path or "/"
        if parsed.query:
            path += f"?{parsed.query}"
        sock = socket.create_connection((host, port), timeout=self._timeout)
        sock.settimeout(self._timeout)
        _ws_handshake(sock, host, port, path)
        self._sock = sock
        return self

    def __exit__(self, _exc_type: object, _exc: object, _tb: object) -> None:
        if self._sock is not None:
            self._sock.close()
            self._sock = None

    def send(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        sock = self._require_sock()
        request_id = self._next_id
        self._next_id += 1
        payload = json.dumps({"id": request_id, "method": method, "params": params or {}}, separators=(",", ":")).encode("utf-8")
        _ws_send_text(sock, payload)
        while True:
            message = _ws_recv_text(sock)
            data = json.loads(message)
            if data.get("id") == request_id:
                return data

    def _require_sock(self) -> socket.socket:
        if self._sock is None:
            raise WebEngineToolError("cdp_session_not_open")
        return self._sock


def _ws_handshake(sock: socket.socket, host: str, port: int, path: str) -> None:
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    sock.sendall(request.encode("ascii"))
    response = _recv_until(sock, b"\r\n\r\n", 65536)
    header = response.decode("iso-8859-1", errors="replace")
    if " 101 " not in header.split("\r\n", 1)[0]:
        raise WebEngineToolError(f"websocket_handshake_failed: {header.splitlines()[0] if header else 'empty_response'}")
    expected = base64.b64encode(hashlib.sha1((key + "258EAFA5-E914-47DA-95CA-C5AB0DC85B11").encode("ascii")).digest()).decode("ascii")
    if expected not in header:
        raise WebEngineToolError("websocket_accept_key_mismatch")


def _ws_send_text(sock: socket.socket, payload: bytes) -> None:
    if len(payload) > MAX_WS_BYTES:
        raise WebEngineToolError("cdp_request_too_large")
    first = 0x81
    mask_bit = 0x80
    header = bytearray([first])
    length = len(payload)
    if length < 126:
        header.append(mask_bit | length)
    elif length < 65536:
        header.extend([mask_bit | 126, *struct.pack("!H", length)])
    else:
        header.extend([mask_bit | 127, *struct.pack("!Q", length)])
    mask = os.urandom(4)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    sock.sendall(bytes(header) + mask + masked)


def _ws_recv_text(sock: socket.socket) -> str:
    first, second = _recv_exact(sock, 2)
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F
    if length == 126:
        length = struct.unpack("!H", _recv_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", _recv_exact(sock, 8))[0]
    if length > MAX_WS_BYTES:
        raise WebEngineToolError("cdp_response_too_large")
    mask = _recv_exact(sock, 4) if masked else b""
    payload = _recv_exact(sock, length)
    if masked:
        payload = bytes(byte ^ mask[index % 4] for index, byte in enumerate(payload))
    if opcode == 0x8:
        raise WebEngineToolError("websocket_closed")
    if opcode != 0x1:
        return _ws_recv_text(sock)
    return payload.decode("utf-8")


def _recv_exact(sock: socket.socket, length: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < length:
        chunk = sock.recv(length - len(chunks))
        if not chunk:
            raise WebEngineToolError("unexpected_socket_eof")
        chunks.extend(chunk)
    return bytes(chunks)


def _recv_until(sock: socket.socket, marker: bytes, limit: int) -> bytes:
    chunks = bytearray()
    while marker not in chunks:
        chunk = sock.recv(4096)
        if not chunk:
            raise WebEngineToolError("unexpected_socket_eof")
        chunks.extend(chunk)
        if len(chunks) > limit:
            raise WebEngineToolError("response_too_large")
    return bytes(chunks)


def _page_summary(page: dict[str, Any]) -> dict[str, object]:
    return {
        "id": page.get("id"),
        "type": page.get("type"),
        "title": page.get("title"),
        "url": page.get("url"),
        "webSocketDebuggerUrl": page.get("webSocketDebuggerUrl"),
    }


def _base_url(host: str | None, port: int | None) -> str:
    return f"http://{_host(host)}:{_port(port)}"


def _host(host: str | None) -> str:
    value = str(host or os.environ.get("DECKHAND_ANKI_CDP_HOST", DEFAULT_HOST)).strip()
    _validate_host(value)
    return value


def _port(port: int | None) -> int:
    raw = port if port is not None else os.environ.get("DECKHAND_ANKI_CDP_PORT", str(DEFAULT_PORT))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_PORT
    return value if 1 <= value <= 65535 else DEFAULT_PORT


def _validate_ws_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme != "ws":
        raise WebEngineToolError("only_ws_urls_are_supported")
    _validate_host(parsed.hostname)
    return url


def _validate_host(host: str | None) -> None:
    if not host or host not in ALLOWED_HOSTS:
        raise WebEngineToolError("cdp_host_must_be_localhost")
