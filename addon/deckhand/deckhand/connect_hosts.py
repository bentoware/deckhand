"""Beginner-facing MCP host recipes shared by onboarding and settings."""

from __future__ import annotations

from typing import Any

CLIENT_CLAUDE_CODE = "claude_code"
CLIENT_CLAUDE_DESKTOP = "claude_desktop"
CLIENT_CODEX_CLI = "codex_cli"
CLIENT_CODEX_DESKTOP = "codex_desktop"
CLIENT_OTHER = "other"

SETUP_ASSET_ROOT = "deckhand/assets/setup"

# The Claude Code plugin bundles this exact endpoint; when the live URL differs
# (custom port) or a token is required, the plugin's static config cannot carry
# it and the recipe falls back to a manual `claude mcp add`.
CLAUDE_PLUGIN_MCP_URL = "http://127.0.0.1:28765/mcp"
CLAUDE_PLUGIN_INSTALL_COMMANDS = (
    "claude plugin marketplace add bentoware/deckhand\n"
    "claude plugin install deckhand@bentoware"
)
CLAUDE_PLUGIN_INSTALL_ONE_LINER = CLAUDE_PLUGIN_INSTALL_COMMANDS.replace("\n", " && ")
CODEX_PLUGIN_MCP_URL = "http://127.0.0.1:28765/mcp"
CODEX_PLUGIN_MARKETPLACE_SOURCE = "https://github.com/bentoware/deckhand"
CODEX_PLUGIN_INSTALL_COMMANDS = (
    f"codex plugin marketplace add {CODEX_PLUGIN_MARKETPLACE_SOURCE}\n"
    "codex plugin add deckhand@deckhand"
)
CODEX_PLUGIN_INSTALL_ONE_LINER = CODEX_PLUGIN_INSTALL_COMMANDS.replace("\n", " && ")

CONNECT_HOSTS: list[dict[str, Any]] = [
    {
        "id": CLIENT_CLAUDE_CODE,
        "label": "Claude Code",
        "tagline": "Install the Deckhand plugin in Anthropic's terminal coding assistant.",
        "difficulty": "Two commands",
        "assetKey": "claude-code",
    },
    {
        "id": CLIENT_CLAUDE_DESKTOP,
        "label": "Claude Desktop",
        "tagline": "Install a Deckhand extension bundle or add Deckhand as a custom connector.",
        "difficulty": "Easiest for Claude",
        "assetKey": "claude-desktop",
    },
    {
        "id": CLIENT_CODEX_CLI,
        "label": "Codex CLI",
        "tagline": "Install the Deckhand plugin in OpenAI's terminal coding assistant.",
        "difficulty": "Two commands",
        "assetKey": "codex-cli",
    },
    {
        "id": CLIENT_CODEX_DESKTOP,
        "label": "Codex Desktop",
        "tagline": "Install the Deckhand marketplace plugin from the Codex desktop app.",
        "difficulty": "Plugins UI",
        "assetKey": "codex-desktop",
    },
    {
        "id": CLIENT_OTHER,
        "label": "Other MCP client",
        "tagline": "Use the standard Streamable HTTP endpoint in any MCP host.",
        "difficulty": "Manual setup",
        "assetKey": "other",
    },
]


def connect_hosts() -> list[dict[str, Any]]:
    """Return hosts in the same alphabetical order used by the setup pill bar."""
    return sorted(CONNECT_HOSTS, key=lambda host: str(host["label"]).lower())


def host_label(client_id: str) -> str:
    return next((str(host["label"]) for host in CONNECT_HOSTS if host["id"] == client_id), "Other MCP client")


def normalize_client_id(client_id: str | None) -> str:
    known = {str(host["id"]) for host in CONNECT_HOSTS}
    if client_id is None:
        return CLIENT_CODEX_DESKTOP
    return client_id if client_id in known else CLIENT_OTHER


def setup_asset_manifest() -> dict[str, list[str]]:
    """Placeholders for packaged walkthrough media added in later release work."""
    return {
        str(host["id"]): [
            f"{SETUP_ASSET_ROOT}/{host['assetKey']}/step-1.webp",
            f"{SETUP_ASSET_ROOT}/{host['assetKey']}/step-2.webp",
            f"{SETUP_ASSET_ROOT}/{host['assetKey']}/walkthrough.mp4",
        ]
        for host in CONNECT_HOSTS
    }


def connect_recipe(client_id: str, mcp_url: str, token: str | None = None) -> dict[str, Any]:
    client_id = normalize_client_id(client_id)
    label = host_label(client_id)
    token_header = f'Authorization: Bearer {token}' if token else ""

    if client_id == CLIENT_CLAUDE_DESKTOP:
        steps = [
            {
                "title": "Install the Deckhand extension",
                "body": 'Drag the Deckhand extension below into Claude Desktop, or click "Save extension..." and open the saved file.',
                "embed": "mcpb",
            },
            {
                "title": "Approve the connector",
                "body": 'Click "Install" in Claude Desktop. The local Deckhand endpoint is already filled in for you.',
            },
            {
                "title": "Confirm it worked",
                "body": 'Start a new Claude chat and ask, "Can you list my Anki decks?"',
            },
            {
                "title": "Manual fallback",
                "body": 'In Claude Desktop, open Settings, then Connectors, choose "Add custom connector", and paste this server URL.',
                "copyLabel": "Server URL",
                "copyText": mcp_url,
                "copyAction": "Copy server URL",
            },
        ]
        if token:
            steps.append(
                {
                    "title": "Token note",
                    "body": "The extension carries your access token automatically. Manual custom connectors usually cannot send it, so use the extension while token security is enabled.",
                }
            )
        return {
            "client": client_id,
            "label": label,
            "intro": "Best path for Claude Desktop: install the Deckhand MCP bundle, then start a fresh Claude chat.",
            "steps": steps,
            "snippet": mcp_url,
            "snippetLabel": "Server URL",
            "primaryAction": "Install extension",
            "assetRefs": setup_asset_manifest()[client_id],
        }

    if client_id == CLIENT_CLAUDE_CODE:
        mcp_command = f"claude mcp add --transport http deckhand {mcp_url}"
        if token:
            mcp_command += f' --header "{token_header}"'
        plugin_fits = not token and mcp_url == CLAUDE_PLUGIN_MCP_URL
        if plugin_fits:
            steps = [
                {"title": "Open Terminal", "body": "Use the same macOS account that runs Anki."},
                {
                    "title": "Install the Deckhand plugin",
                    "body": "Paste the two commands below and press Return. The plugin connects Claude Code to Anki and bundles Deckhand's study skills, and stays current automatically.",
                    "copyLabel": "Terminal commands",
                    "copyText": CLAUDE_PLUGIN_INSTALL_COMMANDS,
                    "copyAction": "Copy commands",
                },
                {"title": "Restart Claude Code", "body": 'Ask it, "Can you list my Anki decks?" to verify the connection.'},
                {
                    "title": "Plugin path blocked?",
                    "body": "As a last resort, add the bare MCP connection instead. This path does not bundle Deckhand's study skills.",
                    "copyLabel": "Fallback command",
                    "copyText": mcp_command,
                    "copyAction": "Copy fallback command",
                },
            ]
            return {
                "client": client_id,
                "label": label,
                "intro": "Best path for Claude Code: install the Deckhand plugin — one install gets the Anki connection plus the study skills.",
                "steps": steps,
                "snippet": CLAUDE_PLUGIN_INSTALL_COMMANDS,
                "snippetLabel": "Terminal commands",
                "primaryAction": "Copy commands",
                "assetRefs": setup_asset_manifest()[client_id],
            }
        reason = (
            "Your access token can't ride along in the plugin's bundled connection"
            if token
            else "Your server runs on a custom address the plugin doesn't know"
        )
        return {
            "client": client_id,
            "label": label,
            "intro": f"{reason}, so connect Claude Code with the command below.",
            "steps": [
                {"title": "Open Terminal", "body": "Use the same macOS account that runs Anki."},
                {
                    "title": "Run the command",
                    "body": "Paste the command below and press Return.",
                    "copyLabel": "Terminal command",
                    "copyText": mcp_command,
                    "copyAction": "Copy command",
                },
                {"title": "Restart Claude Code", "body": 'Ask it, "Can you list my Anki decks?" to verify the connection.'},
                {
                    "title": "Want the study skills too?",
                    "body": (
                        "Install the Deckhand plugin alongside the connection: "
                        f"{CLAUDE_PLUGIN_INSTALL_ONE_LINER}. "
                        "The command above stays the one that connects; the plugin's built-in connection can't send your settings."
                    ),
                },
            ],
            "snippet": mcp_command,
            "snippetLabel": "Terminal command",
            "primaryAction": "Copy command",
            "assetRefs": setup_asset_manifest()[client_id],
        }

    if client_id == CLIENT_CODEX_CLI:
        lines = ["[mcp_servers.deckhand]", f'url = "{mcp_url}"']
        if token:
            lines.append(f'http_headers = {{ "Authorization" = "Bearer {token}" }}')
        config_block = "\n".join(lines)
        plugin_fits = not token and mcp_url == CODEX_PLUGIN_MCP_URL
        if plugin_fits:
            return {
                "client": client_id,
                "label": label,
                "intro": "Best path for Codex CLI: install the Deckhand plugin — one install gets the Anki connection plus the study skills.",
                "steps": [
                    {"title": "Open Terminal", "body": "Use the same macOS account that runs Anki."},
                    {
                        "title": "Install the Deckhand plugin",
                        "body": "Paste the two commands below and press Return. The plugin connects Codex CLI to Anki and bundles Deckhand's study skills.",
                        "copyLabel": "Terminal commands",
                        "copyText": CODEX_PLUGIN_INSTALL_COMMANDS,
                        "copyAction": "Copy commands",
                    },
                    {"title": "Start a new Codex session", "body": 'Ask it, "Can you list my Anki decks?" to verify the connection.'},
                    {
                        "title": "Plugin path blocked?",
                        "body": "As a fallback, add the bare MCP connection in ~/.codex/config.toml instead.",
                        "copyLabel": "config.toml block",
                        "copyText": config_block,
                        "copyAction": "Copy config block",
                    },
                ],
                "snippet": CODEX_PLUGIN_INSTALL_COMMANDS,
                "snippetLabel": "Terminal commands",
                "primaryAction": "Copy commands",
                "assetRefs": setup_asset_manifest()[client_id],
            }
        reason = (
            "Your access token can't ride along in the plugin's bundled connection"
            if token
            else "Your server runs on a custom address the plugin doesn't know"
        )
        return {
            "client": client_id,
            "label": label,
            "intro": f"{reason}, so connect Codex CLI with the config block below.",
            "steps": [
                {"title": "Open the Codex config file", "body": "Open ~/.codex/config.toml. If the file is missing, create it as a plain text file."},
                {
                    "title": "Paste the Deckhand block",
                    "body": "Copy the block below into the file, then save it. Keep any existing settings above or below it.",
                    "copyLabel": "config.toml block",
                    "copyText": config_block,
                    "copyAction": "Copy config block",
                },
                {"title": "Start a new Codex session", "body": "New sessions should include Deckhand's Anki tools."},
                {
                    "title": "Want the study skills too?",
                    "body": (
                        "Install the Deckhand plugin alongside the connection: "
                        f"{CODEX_PLUGIN_INSTALL_ONE_LINER}. "
                        "The config block above stays the one that connects; the plugin's built-in connection can't send your settings."
                    ),
                },
            ],
            "snippet": config_block,
            "snippetLabel": "config.toml block",
            "primaryAction": "Copy config block",
            "assetRefs": setup_asset_manifest()[client_id],
        }

    if client_id == CLIENT_CODEX_DESKTOP:
        lines = ["[mcp_servers.deckhand]", f'url = "{mcp_url}"']
        if token:
            lines.append(f'http_headers = {{ "Authorization" = "Bearer {token}" }}')
        config_block = "\n".join(lines)
        plugin_fits = not token and mcp_url == CODEX_PLUGIN_MCP_URL
        if plugin_fits:
            return {
                "client": client_id,
                "label": label,
                "intro": "Best path for Codex Desktop: add the Deckhand marketplace, then install the Deckhand plugin from the Plugins screen.",
                "steps": [
                    {"title": "Open Plugins", "body": 'In Codex Desktop, choose Plugins in the sidebar, then click "+" and choose "Add marketplace".'},
                    {
                        "title": "Add the Deckhand marketplace",
                        "body": f"Paste the source URL below. Leave Git ref as main unless you need another branch, leave Sparse paths empty, then click Add marketplace.",
                        "copyLabel": "Marketplace source URL",
                        "copyText": CODEX_PLUGIN_MARKETPLACE_SOURCE,
                        "copyAction": "Copy marketplace URL",
                    },
                    {"title": "Install Deckhand", "body": 'Open the Deckhand marketplace tab and click "Add" next to Deckhand.'},
                    {"title": "Start a new chat", "body": 'Ask Codex, "Can you list my Anki decks?" to verify the connection.'},
                    {
                        "title": "Plugin path blocked?",
                        "body": "As a fallback, add the bare MCP connection in ~/.codex/config.toml instead.",
                        "copyLabel": "config.toml block",
                        "copyText": config_block,
                        "copyAction": "Copy config block",
                    },
                ],
                "snippet": CODEX_PLUGIN_MARKETPLACE_SOURCE,
                "snippetLabel": "Marketplace source URL",
                "primaryAction": "Copy marketplace URL",
                "assetRefs": setup_asset_manifest()[client_id],
            }
        reason = (
            "Your access token can't ride along in the plugin's bundled connection"
            if token
            else "Your server runs on a custom address the plugin doesn't know"
        )
        return {
            "client": client_id,
            "label": label,
            "intro": f"{reason}, so connect Codex Desktop with the config block below.",
            "steps": [
                {"title": "Open the Codex config file", "body": "Open ~/.codex/config.toml. If the file is missing, create it as a plain text file."},
                {
                    "title": "Paste the Deckhand block",
                    "body": "Copy the block below into the file, then save it. Keep any existing settings above or below it.",
                    "copyLabel": "config.toml block",
                    "copyText": config_block,
                    "copyAction": "Copy config block",
                },
                {"title": "Restart Codex", "body": "Quit and reopen Codex Desktop. New sessions should include Deckhand's Anki tools."},
                {
                    "title": "Want the study skills too?",
                    "body": (
                        "Install the Deckhand plugin from Codex Desktop's Plugins screen too: "
                        f'add marketplace source {CODEX_PLUGIN_MARKETPLACE_SOURCE}, open the Deckhand tab, then click "Add" next to Deckhand. '
                        "The config block above stays the one that connects; the plugin's built-in connection can't send your settings."
                    ),
                },
            ],
            "snippet": config_block,
            "snippetLabel": "config.toml block",
            "primaryAction": "Copy config block",
            "assetRefs": setup_asset_manifest()[client_id],
        }

    steps = [
        {"title": "Open your MCP host settings", "body": "Find the area where custom MCP servers are added."},
        {"title": "Choose Streamable HTTP", "body": "Deckhand uses one local Streamable HTTP endpoint."},
        {
            "title": "Paste the server URL",
            "body": "Copy this URL into the host's server URL field.",
            "copyLabel": "Server URL",
            "copyText": mcp_url,
            "copyAction": "Copy server URL",
        },
        {"title": "Reconnect tools", "body": "Save, then reconnect or refresh the host's MCP tools list."},
    ]
    if token:
        steps.append({"title": "Add the access token", "body": f'Send this HTTP header if your host supports headers: "{token_header}".'})
    return {
        "client": CLIENT_OTHER,
        "label": label,
        "intro": "Use this path for MCP hosts that support Streamable HTTP servers.",
        "steps": steps,
        "snippet": mcp_url,
        "snippetLabel": "Server URL",
        "primaryAction": "Copy server URL",
        "assetRefs": setup_asset_manifest()[CLIENT_OTHER],
    }


def plain_step_text(recipe: dict[str, Any]) -> list[str]:
    lines = []
    for step in recipe["steps"]:
        line = f"{step['title']}: {step['body']}"
        if step.get("copyText"):
            line += f" {step.get('copyLabel') or 'Copy'}: {step['copyText']}"
        lines.append(line)
    return lines
