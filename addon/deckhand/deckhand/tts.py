"""Text-to-speech SDK for use from ``anki_run_python``.

Typical usage:

    import deckhand.tts as tts
    result = tts.render(
        provider="elevenlabs",
        text="This is a short study hint.",
        out="/tmp/deckhand-hint.mp3",
        stability=0.4,
    )

The module reads provider secrets from Deckhand settings at call time. Schema
and preview helpers intentionally return only non-secret metadata.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from . import settings

OPENAI_VOICES = ["alloy", "ash", "ballad", "coral", "echo", "marin", "sage", "shimmer", "verse"]
GEMINI_VOICES = [
    "Zephyr",
    "Puck",
    "Charon",
    "Kore",
    "Fenrir",
    "Leda",
    "Orus",
    "Aoede",
    "Callirrhoe",
    "Autonoe",
    "Enceladus",
    "Iapetus",
    "Umbriel",
    "Algieba",
    "Despina",
    "Erinome",
    "Algenib",
    "Rasalgethi",
    "Laomedeia",
    "Achernar",
    "Alnilam",
    "Schedar",
    "Gacrux",
    "Pulcherrima",
    "Achird",
    "Zubenelgenubi",
    "Vindemiatrix",
    "Sadachbia",
    "Sadaltager",
    "Sulafat",
]
XAI_VOICES = ["eve", "ara", "rex", "sal", "leo"]
ELEVENLABS_OUTPUT_FORMATS = [
    "mp3_22050_32",
    "mp3_44100_32",
    "mp3_44100_64",
    "mp3_44100_96",
    "mp3_44100_128",
    "mp3_44100_192",
    "pcm_16000",
    "pcm_22050",
    "pcm_24000",
    "pcm_44100",
    "ulaw_8000",
]


class TtsError(RuntimeError):
    pass


def providers() -> list[dict[str, Any]]:
    return [_provider_metadata(name, config) for name, config in settings.tts_settings().items()]


def schema() -> dict[str, Any]:
    return {
        "version": 1,
        "module": "deckhand.tts",
        "usage": "import deckhand.tts as tts; result = tts.render(provider='openai', text='hello', out='/tmp/hello.mp3')",
        "functions": {
            "providers": {"description": "Return configured/unconfigured provider metadata. No network calls."},
            "schema": {"description": "Return this safe parameter schema. No network calls."},
            "preview_request": {"description": "Return a redacted provider request preview. No network calls."},
            "render": {"description": "Render speech to an explicit output path. This is the only networked operation."},
        },
        "common": {
            "requiredOneOf": [["text", "text_file"]],
            "required": ["out"],
            "properties": {
                "provider": {"type": "string", "enum": ["openai", "gemini", "xai", "elevenlabs"]},
                "text": {"type": "string", "description": "Text to synthesize."},
                "text_file": {"type": "path", "description": "UTF-8 text file to synthesize instead of text."},
                "out": {"type": "path", "description": "Required output audio file path."},
                "timeout_seconds": {"type": "number", "minimum": 1, "maximum": 300, "default": 60},
            },
        },
        "providers": {
            "openai": _openai_schema(),
            "gemini": _gemini_schema(),
            "xai": _xai_schema(),
            "elevenlabs": _elevenlabs_schema(),
        },
    }


def preview_request(provider: str, text: str | None = None, text_file: str | None = None, **options: Any) -> dict[str, Any]:
    resolved_text = _resolve_text(text, text_file)
    request = build_request(provider, resolved_text, options)
    return {
        "provider": request["provider"],
        "url": request["url"],
        "method": "POST",
        "headers": _redact_headers(request["headers"]),
        "body": request["body"],
        "model": request["model"],
        "voice": request["voice"],
        "mimeType": request["mimeType"],
        "textLength": len(resolved_text),
    }


def render(
    provider: str,
    text: str | None = None,
    text_file: str | None = None,
    out: str | None = None,
    **options: Any,
) -> dict[str, Any]:
    if not out:
        raise TtsError("tts_output_path_required")
    resolved_text = _resolve_text(text, text_file)
    request = build_request(provider, resolved_text, options)
    timeout = _coerce_float(options.get("timeout_seconds"), 60.0, 1.0, 300.0)
    path = Path(out).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _perform_request(request, timeout)
    audio = _decode_audio_response(request["provider"], payload)
    path.write_bytes(audio)
    return {
        "ok": True,
        "provider": request["provider"],
        "path": str(path),
        "mimeType": request["mimeType"],
        "bytes": path.stat().st_size,
        "model": request["model"],
        "voice": request["voice"],
        "responseMeta": request.get("responseMeta", {}),
    }


def build_request(provider: str, text: str, options: dict[str, Any] | None = None) -> dict[str, Any]:
    normalized = _normalize_provider(provider)
    options = options or {}
    config = settings.tts_provider_settings(normalized)
    if normalized == "openai":
        return _build_openai_request(text, config, options)
    if normalized == "gemini":
        return _build_gemini_request(text, config, options)
    if normalized == "xai":
        return _build_xai_request(text, config, options)
    if normalized == "elevenlabs":
        return _build_elevenlabs_request(text, config, options)
    raise TtsError(f"unsupported_tts_provider:{provider}")


def wav_from_pcm16(pcm: bytes, sample_rate: int = 24000) -> bytes:
    data_size = len(pcm)
    header = bytearray(44)
    header[0:4] = b"RIFF"
    header[4:8] = (36 + data_size).to_bytes(4, "little")
    header[8:12] = b"WAVE"
    header[12:16] = b"fmt "
    header[16:20] = (16).to_bytes(4, "little")
    header[20:22] = (1).to_bytes(2, "little")
    header[22:24] = (1).to_bytes(2, "little")
    header[24:28] = int(sample_rate).to_bytes(4, "little")
    header[28:32] = int(sample_rate * 2).to_bytes(4, "little")
    header[32:34] = (2).to_bytes(2, "little")
    header[34:36] = (16).to_bytes(2, "little")
    header[36:40] = b"data"
    header[40:44] = data_size.to_bytes(4, "little")
    return bytes(header) + pcm


def _provider_metadata(name: str, config: dict[str, Any]) -> dict[str, Any]:
    missing = _missing_config(name, config)
    return {
        "name": name,
        "configured": not missing,
        "missingConfig": missing,
        "secretSource": "deckhand_settings",
        "schema": schema()["providers"].get(name, {}),
    }


def _missing_config(provider: str, config: dict[str, Any]) -> list[str]:
    missing = []
    if not str(config.get("apiKey") or "").strip():
        missing.append("credential")
    if provider == "elevenlabs" and not str(config.get("voiceId") or "").strip():
        missing.append("voiceId")
    return missing


def _openai_schema() -> dict[str, Any]:
    return {
        "configuredBy": ["credential"],
        "outputMimeTypes": ["audio/mpeg", "audio/wav", "audio/opus", "audio/aac", "audio/flac"],
        "properties": {
            "model": {"type": "string", "default": "gpt-4o-mini-tts"},
            "voice": {"type": "string", "enum": OPENAI_VOICES, "default": "alloy"},
            "response_format": {"type": "string", "enum": ["mp3", "opus", "aac", "flac", "wav", "pcm"], "default": "mp3"},
        },
    }


def _gemini_schema() -> dict[str, Any]:
    return {
        "configuredBy": ["credential"],
        "outputMimeTypes": ["audio/wav"],
        "properties": {
            "model": {"type": "string", "default": "gemini-3.1-flash-tts-preview"},
            "voice": {"type": "string", "enum": GEMINI_VOICES, "default": "Kore"},
            "language_code": {"type": "string", "default": ""},
            "prompt_prefix": {"type": "string", "default": "Say naturally and clearly:"},
        },
    }


def _xai_schema() -> dict[str, Any]:
    return {
        "configuredBy": ["credential"],
        "outputMimeTypes": ["audio/mpeg"],
        "properties": {
            "voice": {"type": "string", "enum": XAI_VOICES, "default": "eve"},
            "language": {"type": "string", "default": "en"},
            "sample_rate": {"type": "integer", "default": 24000},
            "bit_rate": {"type": "integer", "default": 128000},
        },
    }


def _elevenlabs_schema() -> dict[str, Any]:
    return {
        "configuredBy": ["credential", "voiceId"],
        "outputMimeTypes": ["audio/mpeg", "audio/wav", "audio/pcm", "audio/basic"],
        "properties": {
            "voice_id": {"type": "string", "defaultSource": "deckhand_settings"},
            "model_id": {"type": "string", "default": "eleven_multilingual_v2"},
            "output_format": {"type": "string", "enum": ELEVENLABS_OUTPUT_FORMATS, "default": "mp3_44100_128"},
            "language_code": {"type": "string", "default": ""},
            "stability": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.5},
            "similarity_boost": {"type": "number", "minimum": 0, "maximum": 1, "default": 0.75},
            "style": {"type": "number", "minimum": 0, "maximum": 1, "default": 0},
            "speed": {"type": "number", "minimum": 0.7, "maximum": 1.2, "default": 1},
            "use_speaker_boost": {"type": "boolean", "default": True},
            "seed": {"type": "integer"},
            "previous_text": {"type": "string"},
            "next_text": {"type": "string"},
            "previous_request_ids": {"type": "array", "items": {"type": "string"}},
            "next_request_ids": {"type": "array", "items": {"type": "string"}},
            "apply_text_normalization": {"type": "string", "enum": ["auto", "on", "off"], "default": "auto"},
            "apply_language_text_normalization": {"type": "boolean", "default": False},
            "enable_logging": {"type": "boolean", "default": True},
        },
    }


def _build_openai_request(text: str, config: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    api_key = _required_api_key("openai", config)
    model = _option(options, "model", config.get("model"))
    voice = _option(options, "voice", config.get("voice"))
    response_format = _option(options, "response_format", config.get("responseFormat"))
    body = {
        "model": model,
        "voice": voice,
        "input": text,
        "response_format": response_format,
    }
    return {
        "provider": "openai",
        "url": str(config.get("url")),
        "headers": {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        "body": body,
        "bodyBytes": _json_bytes(body),
        "model": model,
        "voice": voice,
        "mimeType": _openai_mime_type(str(response_format)),
    }


def _build_gemini_request(text: str, config: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    api_key = _required_api_key("gemini", config)
    model = _option(options, "model", config.get("model"))
    voice = _option(options, "voice", config.get("voice"))
    language_code = _option(options, "language_code", config.get("languageCode"))
    prompt_prefix = _option(options, "prompt_prefix", config.get("promptPrefix"))
    speech_config = {
        "voiceConfig": {
            "prebuiltVoiceConfig": {
                "voiceName": voice,
            },
        },
    }
    if str(language_code or "").strip():
        speech_config["languageCode"] = str(language_code).strip()
    body = {
        "contents": [{"role": "user", "parts": [{"text": f"{str(prompt_prefix).strip()} {text}".strip()}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": speech_config,
        },
    }
    base_url = str(config.get("url")).rstrip("/")
    url = f"{base_url}/models/{quote(str(model), safe='')}:generateContent"
    return {
        "provider": "gemini",
        "url": url,
        "headers": {"x-goog-api-key": api_key, "Content-Type": "application/json"},
        "body": body,
        "bodyBytes": _json_bytes(body),
        "model": model,
        "voice": voice,
        "mimeType": "audio/wav",
        "responseMeta": {"pcmSampleRate": 24000},
    }


def _build_xai_request(text: str, config: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    api_key = _required_api_key("xai", config)
    voice = _option(options, "voice", config.get("voice"))
    language = _option(options, "language", config.get("language"))
    sample_rate = _coerce_int(_option(options, "sample_rate", config.get("sampleRate")), 24000)
    bit_rate = _coerce_int(_option(options, "bit_rate", config.get("bitRate")), 128000)
    body = {
        "text": text,
        "voice_id": voice,
        "language": language,
        "output_format": {
            "codec": "mp3",
            "sample_rate": sample_rate,
            "bit_rate": bit_rate,
        },
    }
    return {
        "provider": "xai",
        "url": str(config.get("url")),
        "headers": {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        "body": body,
        "bodyBytes": _json_bytes(body),
        "model": "xai-tts",
        "voice": voice,
        "mimeType": "audio/mpeg",
    }


def _build_elevenlabs_request(text: str, config: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    api_key = _required_api_key("elevenlabs", config)
    voice_id = _option(options, "voice_id", config.get("voiceId"))
    if not str(voice_id or "").strip():
        raise TtsError("elevenlabs_voice_id_required")
    model_id = _option(options, "model_id", config.get("modelId"))
    output_format = _option(options, "output_format", config.get("outputFormat"))
    enable_logging = _coerce_bool(_option(options, "enable_logging", config.get("enableLogging")), True)
    language_code = _option(options, "language_code", config.get("languageCode"))
    body: dict[str, Any] = {
        "text": text,
        "model_id": model_id,
        "voice_settings": {
            "stability": _coerce_float(_option(options, "stability", config.get("stability")), 0.5, 0, 1),
            "similarity_boost": _coerce_float(_option(options, "similarity_boost", config.get("similarityBoost")), 0.75, 0, 1),
            "style": _coerce_float(_option(options, "style", config.get("style")), 0, 0, 1),
            "speed": _coerce_float(_option(options, "speed", config.get("speed")), 1, 0.7, 1.2),
            "use_speaker_boost": _coerce_bool(_option(options, "use_speaker_boost", config.get("useSpeakerBoost")), True),
        },
        "apply_text_normalization": _option(options, "apply_text_normalization", config.get("applyTextNormalization")),
        "apply_language_text_normalization": _coerce_bool(
            _option(options, "apply_language_text_normalization", config.get("applyLanguageTextNormalization")),
            False,
        ),
    }
    if str(language_code or "").strip():
        body["language_code"] = str(language_code).strip()
    for option_key in ("seed", "previous_text", "next_text", "previous_request_ids", "next_request_ids"):
        if option_key in options and options[option_key] not in (None, ""):
            body[option_key] = options[option_key]
    base_url = str(config.get("url")).rstrip("/")
    query = urlencode({"output_format": output_format, "enable_logging": str(enable_logging).lower()})
    return {
        "provider": "elevenlabs",
        "url": f"{base_url}/{quote(str(voice_id), safe='')}?{query}",
        "headers": {"xi-api-key": api_key, "Content-Type": "application/json"},
        "body": body,
        "bodyBytes": _json_bytes(body),
        "model": model_id,
        "voice": voice_id,
        "mimeType": _elevenlabs_mime_type(str(output_format)),
    }


def _perform_request(request: dict[str, Any], timeout: float) -> tuple[dict[str, str], bytes]:
    req = Request(request["url"], data=request["bodyBytes"], headers=request["headers"], method="POST")
    try:
        with urlopen(req, timeout=timeout) as response:  # noqa: S310 - explicit user-configured provider endpoint
            return dict(response.headers.items()), response.read()
    except HTTPError as exc:
        body = exc.read()
        detail = body.decode("utf-8", errors="replace")[:800]
        raise TtsError(f"{request['provider']}_tts_http_{exc.code}:{_redact_text(detail)}") from exc
    except URLError as exc:
        raise TtsError(f"{request['provider']}_tts_network_error:{_redact_text(str(exc))}") from exc


def _decode_audio_response(provider: str, payload: tuple[dict[str, str], bytes]) -> bytes:
    _headers, body = payload
    if provider != "gemini":
        return body
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TtsError("gemini_tts_response_not_json") from exc
    pcm = _extract_gemini_inline_audio(data)
    return wav_from_pcm16(pcm, 24000)


def _extract_gemini_inline_audio(payload: Any) -> bytes:
    for candidate in _list_field(payload, "candidates"):
        content = _dict_field(candidate, "content")
        for part in _list_field(content, "parts"):
            inline = _dict_field(part, "inlineData") or _dict_field(part, "inline_data")
            data = inline.get("data") if isinstance(inline, dict) else None
            if isinstance(data, str) and data.strip():
                return base64.b64decode(data)
    raise TtsError("gemini_tts_missing_inline_audio")


def _resolve_text(text: str | None, text_file: str | None) -> str:
    if bool(text and str(text).strip()) == bool(text_file and str(text_file).strip()):
        raise TtsError("tts_requires_exactly_one_of_text_or_text_file")
    if text_file:
        value = Path(text_file).expanduser().read_text(encoding="utf-8")
    else:
        value = str(text or "")
    value = value.strip()
    if not value:
        raise TtsError("tts_text_required")
    return value


def _normalize_provider(provider: str) -> str:
    value = str(provider or "").strip().lower()
    aliases = {"grok": "xai", "eleven": "elevenlabs"}
    value = aliases.get(value, value)
    if value not in {"openai", "gemini", "xai", "elevenlabs"}:
        raise TtsError(f"unsupported_tts_provider:{provider}")
    return value


def _required_api_key(provider: str, config: dict[str, Any]) -> str:
    api_key = str(config.get("apiKey") or "").strip()
    if not api_key:
        raise TtsError(f"{provider}_api_key_required")
    return api_key


def _option(options: dict[str, Any], snake_name: str, default: Any) -> Any:
    camel_name = _snake_to_camel(snake_name)
    return options[snake_name] if snake_name in options else options.get(camel_name, default)


def _snake_to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part[:1].upper() + part[1:] for part in tail)


def _json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    redacted = {}
    for key, value in headers.items():
        redacted[key] = "[redacted]" if key.lower() in {"authorization", "x-goog-api-key", "xi-api-key"} else value
    return redacted


def _redact_text(value: str) -> str:
    lowered = value.lower()
    return "[redacted]" if any(marker in lowered for marker in ("api_key", "token", "secret", "authorization", "xi-api-key")) else value


def _coerce_int(value: Any, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _coerce_float(value: Any, fallback: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = fallback
    return max(minimum, min(maximum, parsed))


def _coerce_bool(value: Any, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on"}:
            return True
        if lowered in {"0", "false", "no", "off"}:
            return False
    return fallback


def _openai_mime_type(response_format: str) -> str:
    return {
        "mp3": "audio/mpeg",
        "opus": "audio/opus",
        "aac": "audio/aac",
        "flac": "audio/flac",
        "wav": "audio/wav",
        "pcm": "audio/pcm",
    }.get(response_format, "audio/mpeg")


def _elevenlabs_mime_type(output_format: str) -> str:
    if output_format.startswith("mp3_"):
        return "audio/mpeg"
    if output_format.startswith("ulaw_"):
        return "audio/basic"
    if output_format.startswith("pcm_"):
        return "audio/pcm"
    return "application/octet-stream"


def _list_field(value: Any, key: str) -> list[Any]:
    if not isinstance(value, dict):
        return []
    field = value.get(key)
    return field if isinstance(field, list) else []


def _dict_field(value: Any, key: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    field = value.get(key)
    return field if isinstance(field, dict) else {}
