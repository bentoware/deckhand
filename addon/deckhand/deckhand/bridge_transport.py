from __future__ import annotations

import base64
import json
import os
import socket
import struct
import threading
import time
from collections.abc import Callable
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .bridge import bridge_status
from .capabilities import is_anki_bridge_tool_name
from .direct_executor import DirectExecutor


DEFAULT_URL = "ws://127.0.0.1:28765/ws/anki"
BRIDGE_PROTOCOL_VERSION = "deckhand.ankiBridge.v1"


class SafeBridgeClient:
    def __init__(
        self,
        *,
        executor: DirectExecutor,
        registry_provider: Callable[[], dict[str, Any]],
        logger: Callable[..., None] | None = None,
        executor_runner: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
        url: str | None = None,
    ) -> None:
        self._executor = executor
        self._registry_provider = registry_provider
        self._logger = logger
        self._executor_runner = executor_runner
        self._url = url or os.environ.get("DECKHAND_SAFE_BRIDGE_URL", DEFAULT_URL)
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if os.environ.get("DECKHAND_SAFE_BRIDGE_DISABLED") == "1":
            self._log("safe_bridge.disabled")
            return
        if self._thread and self._thread.is_alive():
            return
        self._log("safe_bridge.starting", url=self._url, tokenPresent=bool(companion_token()))
        self._thread = threading.Thread(target=self._run_forever, name="deckhand-safe-bridge", daemon=True)
        self._thread.start()
        self._log("safe_bridge.thread_started", name=self._thread.name)

    def _run_forever(self) -> None:
        while True:
            self._run_once()
            time.sleep(float(os.environ.get("DECKHAND_SAFE_BRIDGE_RETRY_SECONDS", "2")))

    def _run_once(self) -> None:
        try:
            with connect_websocket(self._url) as sock:
                bridge_status.update("connected", f"Anki bridge connected: {self._url}")
                self._log("safe_bridge.connected", url=self._url)
                send_text(sock, json.dumps(bridge_hello_payload(self._registry_provider())))
                if urlparse(self._url).path == "/ws/anki":
                    first = recv_text(sock)
                    if first is not None:
                        accepted = self._handle_control_message(first)
                        if not accepted:
                            return
                while True:
                    message = recv_text(sock)
                    if message is None:
                        break
                    self._handle_message(sock, message)
        except Exception as exc:  # noqa: BLE001 - transport must not break Anki startup
            bridge_status.update("disconnected", f"Anki bridge unavailable: {exc}")
            self._log("safe_bridge.disconnected", url=self._url, error=str(exc))

    def _handle_message(self, sock: socket.socket, raw: str) -> None:
        self._log("safe_bridge.message_received", raw=raw)
        message = json.loads(raw)
        if message.get("method") in {"anki_bridge_accept", "anki_bridge_reject"}:
            self._handle_control_message(raw)
            return
        if message.get("method") != "tool.call":
            return
        params = message.get("params") or {}
        tool = str(params.get("tool", ""))
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        if self._executor_runner:
            result = self._executor_runner(tool, arguments)
        else:
            result = self._executor.call(tool, arguments).to_dict()
        result.setdefault("durationMs", 0)
        params = {"tool": tool, **result}
        response = {
            "id": message.get("id"),
            "method": "tool.result",
            "params": params,
        }
        send_text(sock, json.dumps(response, sort_keys=True))
        self._log("safe_bridge.tool_result_sent", tool=tool, ok=bool(result.get("ok")))

    def _log(self, event: str, **payload: object) -> None:
        if self._logger:
            self._logger(event, **payload)

    def _handle_control_message(self, raw: str) -> bool:
        message = json.loads(raw)
        method = message.get("method")
        if method == "anki_bridge_accept":
            self._log("safe_bridge.accepted", protocol=message.get("params", {}).get("protocolVersion"))
            return True
        if method == "anki_bridge_reject":
            error = str(message.get("params", {}).get("error", "bridge_rejected"))
            bridge_status.update("disconnected", f"Anki bridge rejected: {error}")
            self._log("safe_bridge.rejected", error=error)
            return False
        return True


def bridge_hello_payload(registry: dict[str, Any], env: dict[str, str] | None = None) -> dict[str, Any]:
    env = env or os.environ
    raw_tools = registry.get("tools") if isinstance(registry.get("tools"), list) else []
    tools = [
        tool
        for tool in raw_tools
        if isinstance(tool, dict) and is_anki_bridge_tool_name(str(tool.get("name", "")))
    ]
    return {
        "method": "anki_bridge_hello",
        "params": {
            "protocolVersion": BRIDGE_PROTOCOL_VERSION,
            "addonVersion": str(registry.get("addonVersion") or "0.1.0"),
            "profileHash": str(registry.get("profileHash") or registry.get("bridgeId") or "anki-local-profile"),
            "collectionHash": registry.get("collectionHash"),
            "capabilities": registry.get("capabilities", {}),
            "tools": tools,
            "pairingToken": env.get("DECKHAND_ANKI_BRIDGE_TOKEN") or env.get("DECKHAND_COMPANION_TOKEN", ""),
        },
    }


def companion_token(env: dict[str, str] | None = None) -> str:
    env = env or os.environ
    return env.get("DECKHAND_COMPANION_TOKEN", "")


def with_companion_token(url: str, token: str | None = None) -> str:
    token = token if token is not None else companion_token()
    if not token:
        return url
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query.setdefault("token", token)
    return urlunparse(parsed._replace(query=urlencode(query)))


def connect_websocket(url: str) -> socket.socket:
    token = companion_token()
    url = with_companion_token(url, token)
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 80
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    key = base64.b64encode(os.urandom(16)).decode("ascii")
    authorization = f"Authorization: Bearer {token}\r\n" if token else ""
    sock = socket.create_connection((host, port), timeout=1.5)
    request = (
        f"GET {path} HTTP/1.1\r\n"
        f"Host: {host}:{port}\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"{authorization}"
        f"Sec-WebSocket-Key: {key}\r\n"
        "Sec-WebSocket-Version: 13\r\n"
        "\r\n"
    )
    sock.sendall(request.encode("ascii"))
    response = sock.recv(4096).decode("latin1", errors="replace")
    if " 101 " not in response.split("\r\n", 1)[0]:
        status_line = response.split("\r\n", 1)[0] or "missing_status"
        sock.close()
        raise RuntimeError(f"websocket_upgrade_failed:{status_line}")
    sock.settimeout(None)
    return sock


def send_text(sock: socket.socket, payload: str) -> None:
    data = payload.encode("utf-8")
    mask = os.urandom(4)
    header = bytearray([0x81])
    if len(data) < 126:
        header.append(0x80 | len(data))
    elif len(data) <= 0xFFFF:
        header.append(0x80 | 126)
        header.extend(struct.pack("!H", len(data)))
    else:
        header.append(0x80 | 127)
        header.extend(struct.pack("!Q", len(data)))
    header.extend(mask)
    masked = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
    sock.sendall(bytes(header) + masked)


def recv_text(sock: socket.socket) -> str | None:
    try:
        first = sock.recv(2)
    except TimeoutError:
        return None
    if not first:
        return None
    opcode = first[0] & 0x0F
    if opcode == 0x8:
        return None
    length = first[1] & 0x7F
    if length == 126:
        length = struct.unpack("!H", _recv_exact(sock, 2))[0]
    elif length == 127:
        length = struct.unpack("!Q", _recv_exact(sock, 8))[0]
    masked = bool(first[1] & 0x80)
    mask = _recv_exact(sock, 4) if masked else b""
    data = _recv_exact(sock, length)
    if masked:
        data = bytes(byte ^ mask[index % 4] for index, byte in enumerate(data))
    return data.decode("utf-8")


def _recv_exact(sock: socket.socket, count: int) -> bytes:
    chunks = bytearray()
    deadline = time.time() + 10
    while len(chunks) < count:
        if time.time() > deadline:
            raise TimeoutError("websocket_read_timeout")
        chunk = sock.recv(count - len(chunks))
        if not chunk:
            raise EOFError("websocket_closed")
        chunks.extend(chunk)
    return bytes(chunks)
