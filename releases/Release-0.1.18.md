# Deckhand 0.1.18

This release adds the first Deckhand-managed text-to-speech surface and makes backup guidance clearer for agents doing risky collection work.

## Added

- **Optional TTS provider settings** - the management dialog now has a TTS tab for OpenAI, Gemini, xAI/Grok, and ElevenLabs settings, with provider keys stored in Deckhand settings.
- **A code-mode TTS SDK** - agents running Python inside Anki can inspect safe TTS schemas and render speech through `deckhand.tts` without seeing secret values.
- **TTS onboarding copy** - the welcome flow now points users to optional voice setup for spoken hints, examples, and review audio.

## Improved

- **Backup guidance is clearer** - the public backup tool description now says native Anki backups do not include media files and points media-sensitive work to media-inclusive package exports.
- **The README links to AnkiWeb** - the project page now includes the published AnkiWeb add-on listing alongside the release download link.

## Install

1. Download `deckhand.ankiaddon` below.
2. In Anki: **Tools -> Add-ons -> Install from file...**, pick the download, restart Anki.
