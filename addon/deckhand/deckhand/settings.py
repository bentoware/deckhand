"""Persisted user settings for the Deckhand add-on.

Settings live in ``work_root()/settings.json`` so they survive Anki restarts.
Environment variables still win over persisted values; the management UI is
the supported way for end users to change these, while env vars remain the
developer override surface.
"""

from __future__ import annotations

import json
import os
import secrets
from pathlib import Path
from typing import Any

from .state_paths import work_root

DEFAULT_COMPANION_PORT = 28765

_SETTING_DEFAULTS: dict[str, Any] = {
    "companionPort": DEFAULT_COMPANION_PORT,
    "companionAutostart": True,
    "requireMcpToken": False,
    "cdpBannerDismissed": False,
    "updateCheckEnabled": True,
    "skippedUpdateVersion": "",
    "lastUpdateCheckMs": 0,
    "skillsAutoUpdateEnabled": True,
    "lastSkillsSyncMs": 0,
    "welcomeShown": False,
    "tts": {
        "openai": {
            "apiKey": "",
            "model": "gpt-4o-mini-tts",
            "voice": "alloy",
            "responseFormat": "mp3",
            "url": "https://api.openai.com/v1/audio/speech",
        },
        "gemini": {
            "apiKey": "",
            "model": "gemini-3.1-flash-tts-preview",
            "voice": "Kore",
            "languageCode": "",
            "promptPrefix": "Say naturally and clearly:",
            "url": "https://generativelanguage.googleapis.com/v1beta",
        },
        "xai": {
            "apiKey": "",
            "voice": "eve",
            "language": "en",
            "sampleRate": 24000,
            "bitRate": 128000,
            "url": "https://api.x.ai/v1/tts",
        },
        "elevenlabs": {
            "apiKey": "",
            "voiceId": "",
            "modelId": "eleven_multilingual_v2",
            "outputFormat": "mp3_44100_128",
            "languageCode": "",
            "stability": 0.5,
            "similarityBoost": 0.75,
            "style": 0,
            "speed": 1,
            "useSpeakerBoost": True,
            "applyTextNormalization": "auto",
            "applyLanguageTextNormalization": False,
            "enableLogging": True,
            "url": "https://api.elevenlabs.io/v1/text-to-speech",
        },
    },
}


def settings_path() -> Path:
    return work_root() / "settings.json"


def token_path() -> Path:
    return work_root() / "companion-token"


def read_settings() -> dict[str, Any]:
    try:
        payload = json.loads(settings_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def get(name: str, default: Any | None = None) -> Any:
    if default is None:
        default = _SETTING_DEFAULTS.get(name)
    return read_settings().get(name, default)


def update(values: dict[str, Any]) -> dict[str, Any]:
    payload = {**read_settings(), **values}
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def tts_settings() -> dict[str, Any]:
    configured = get("tts", {})
    configured = configured if isinstance(configured, dict) else {}
    defaults = _SETTING_DEFAULTS["tts"]
    merged: dict[str, Any] = {}
    for provider, provider_defaults in defaults.items():
        provider_config = configured.get(provider, {})
        if not isinstance(provider_config, dict):
            provider_config = {}
        merged[provider] = {**provider_defaults, **provider_config}
    return _apply_tts_env_overrides(merged)


def tts_provider_settings(provider: str) -> dict[str, Any]:
    return dict(tts_settings().get(provider, {}))


def set_tts_provider_settings(provider: str, values: dict[str, Any]) -> dict[str, Any]:
    current = read_settings()
    tts = current.get("tts", {})
    if not isinstance(tts, dict):
        tts = {}
    provider_config = tts.get(provider, {})
    if not isinstance(provider_config, dict):
        provider_config = {}
    tts[provider] = {**provider_config, **values}
    return update({"tts": tts})


def _apply_tts_env_overrides(config: dict[str, Any]) -> dict[str, Any]:
    overrides = {
        ("openai", "apiKey"): "DECKHAND_OPENAI_API_KEY",
        ("openai", "url"): "DECKHAND_OPENAI_SPEECH_URL",
        ("gemini", "apiKey"): "DECKHAND_GEMINI_API_KEY",
        ("gemini", "url"): "DECKHAND_GEMINI_API_BASE_URL",
        ("xai", "apiKey"): "DECKHAND_XAI_API_KEY",
        ("xai", "url"): "DECKHAND_XAI_TTS_URL",
        ("elevenlabs", "apiKey"): "DECKHAND_ELEVENLABS_API_KEY",
        ("elevenlabs", "url"): "DECKHAND_ELEVENLABS_TTS_URL",
    }
    for (provider, key), env_name in overrides.items():
        value = os.environ.get(env_name)
        if value:
            config.setdefault(provider, {})[key] = value
    return config


def companion_port() -> int:
    return _coerce_port(get("companionPort"), DEFAULT_COMPANION_PORT)


def set_companion_port(port: int) -> int:
    coerced = _coerce_port(port, DEFAULT_COMPANION_PORT)
    update({"companionPort": coerced})
    return coerced


def companion_autostart() -> bool:
    return bool(get("companionAutostart"))


def set_companion_autostart(enabled: bool) -> None:
    update({"companionAutostart": bool(enabled)})


def require_mcp_token() -> bool:
    return bool(get("requireMcpToken"))


def set_require_mcp_token(enabled: bool) -> None:
    update({"requireMcpToken": bool(enabled)})


def cdp_banner_dismissed() -> bool:
    return bool(get("cdpBannerDismissed"))


def set_cdp_banner_dismissed(dismissed: bool = True) -> None:
    update({"cdpBannerDismissed": bool(dismissed)})


def update_check_enabled() -> bool:
    return bool(get("updateCheckEnabled"))


def set_update_check_enabled(enabled: bool) -> None:
    update({"updateCheckEnabled": bool(enabled)})


def skipped_update_version() -> str:
    return str(get("skippedUpdateVersion") or "")


def set_skipped_update_version(version: str) -> None:
    update({"skippedUpdateVersion": str(version)})


def last_update_check_ms() -> int:
    try:
        return int(get("lastUpdateCheckMs") or 0)
    except (TypeError, ValueError):
        return 0


def set_last_update_check_ms(value: int) -> None:
    update({"lastUpdateCheckMs": int(value)})


def skills_auto_update_enabled() -> bool:
    return bool(get("skillsAutoUpdateEnabled"))


def set_skills_auto_update_enabled(enabled: bool) -> None:
    update({"skillsAutoUpdateEnabled": bool(enabled)})


def last_skills_sync_ms() -> int:
    try:
        return int(get("lastSkillsSyncMs") or 0)
    except (TypeError, ValueError):
        return 0


def set_last_skills_sync_ms(value: int) -> None:
    update({"lastSkillsSyncMs": int(value)})


def welcome_shown() -> bool:
    return bool(get("welcomeShown"))


def set_welcome_shown(shown: bool = True) -> None:
    update({"welcomeShown": bool(shown)})


def persistent_token() -> str:
    """Return the stable companion token, creating it on first use.

    Client configs embed this token when MCP auth is enabled, so it must stay
    identical across Anki restarts (unlike the per-session in-memory token).
    """
    path = token_path()
    try:
        existing = path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    except OSError:
        pass
    token = secrets.token_urlsafe(32)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token + "\n", encoding="utf-8")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return token


def regenerate_persistent_token() -> str:
    try:
        token_path().unlink()
    except OSError:
        pass
    return persistent_token()


def _coerce_port(value: Any, fallback: int) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError):
        return fallback
    return port if 1 <= port <= 65535 else fallback
