# Discord MCP Server

A Model Context Protocol (MCP) server that lets LLMs read messages, discover channels, send messages, and monitor Discord communities using web scraping.

## Features

- List Discord servers and channels you have access to
- Read recent messages with time filtering (newest first)
- Send messages to Discord channels (automatically splits long messages)
- Web scraping approach — works with any Discord server you can access as a user
- No bot permissions or API tokens required

## Quick Start with Claude Code

```bash
# Clone and install
git clone https://github.com/elyxlz/discord-mcp.git
cd discord-mcp
uv sync
uv run playwright install

# Add to Claude Code (adjust the path to match your clone location)
claude mcp add discord-mcp -s user \
  -e DISCORD_EMAIL=your_email@example.com \
  -e DISCORD_PASSWORD=your_password \
  -e DISCORD_HEADLESS=true \
  -- uv run --directory /path/to/discord-mcp python main.py
```

### Usage Examples

```
# List your Discord servers
use get_servers to show me all my Discord servers

# Read recent messages (max_messages is required)
read the last 20 messages from channel ID 123 in server ID 456

# Send a message (long messages automatically split)
send "Hello!" to channel 123 in server 456

# Monitor communities
summarize discussions from the last 24 hours across my Discord servers
```

## Available Tools

- **`get_servers`** — List all Discord servers you have access to
- **`get_channels(server_id)`** — List channels in a specific server
- **`read_messages(server_id, channel_id, max_messages, hours_back?)`** — Read recent messages, newest first (`hours_back` defaults to 24)
- **`send_message(server_id, channel_id, content)`** — Send messages to channels (automatically splits messages longer than 2000 characters)

## Manual Setup

### Prerequisites
- Python 3.12+ with `uv` package manager
- Discord account credentials

### Installation
```bash
git clone https://github.com/elyxlz/discord-mcp.git
cd discord-mcp
uv sync
uv run playwright install
```

### Configuration
Create a `.env` file:
```env
DISCORD_EMAIL=your_email@example.com
DISCORD_PASSWORD=your_password
DISCORD_HEADLESS=true
```

### Run Server
```bash
uv run python main.py
```

## Claude Desktop Integration

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "discord": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/discord-mcp", "python", "main.py"],
      "env": {
        "DISCORD_EMAIL": "your_email@example.com",
        "DISCORD_PASSWORD": "your_password",
        "DISCORD_HEADLESS": "true"
      }
    }
  }
}
```

## Performance

The server keeps a persistent browser session alive across tool calls:

- **First call** (~8–15s): launches Chromium, logs in, saves cookies
- **Subsequent calls** (~1–3s): reuses the session; reads messages via Discord's REST API rather than DOM scraping
- **Cookie persistence**: saved at `~/.discord_mcp_cookies.json`, so re-login is only needed when the session expires

## Development

```bash
# Type checking
uv run pyright

# Formatting
uvx ruff format .

# Linting
uvx ruff check --fix --unsafe-fixes .

# Run all tests (credentials required via .env or environment variables)
uv run pytest -v tests/

# Run the send_message test (requires a writable test channel)
DISCORD_TEST_SERVER_ID=your_server_id DISCORD_TEST_CHANNEL_ID=your_channel_id uv run pytest -v tests/ -k send_message
```

## Security Notes

- Consider using a dedicated Discord account for automation
- The server includes a 0.5s delay between split messages to avoid rate limiting
- Always use `DISCORD_HEADLESS=true` in production
- If 2FA is enabled on your account, the first login will pause and wait for you to complete it manually (the browser is visible during this step if `DISCORD_HEADLESS=false`)

## Troubleshooting

- **Login issues**: Verify credentials are correct; set `DISCORD_HEADLESS=false` to see the browser during login
- **2FA**: Set `DISCORD_HEADLESS=false` for the first run so you can complete the 2FA prompt interactively — cookies are saved afterwards
- **Browser errors**: Run `uv run playwright install --force`
- **Stale session**: Delete `~/.discord_mcp_cookies.json` to force a fresh login
- **Rate limits**: Reduce `max_messages`; the REST API path handles this gracefully with automatic fallback to DOM scraping

## Legal Notice

Ensure compliance with Discord's Terms of Service. Only access information you would normally have access to as a user. Use for legitimate monitoring and research purposes.
