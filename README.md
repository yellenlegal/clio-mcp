# Clio MCP Server

A [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server that connects Claude Code (or any MCP client) to the [Clio](https://www.clio.com/) legal practice management API. This allows AI assistants to create time entries, expense entries, tasks, calendar events, and more directly in Clio.

## Tools

| Tool | Description |
|------|-------------|
| `create_time_entry` | Create a billable time entry |
| `create_expense_entry` | Create a hard cost / expense entry |
| `create_matter` | Create a new matter |
| `create_contact` | Create a person or company contact |
| `create_task` | Create a task linked to a matter |
| `list_calendar_entries` | List/search calendar events |
| `create_calendar_entry` | Create a calendar event |
| `update_calendar_entry` | Update an existing calendar event |
| `search_contacts` | Search contacts by name |
| `search_matters` | Search matters by description/number |
| `list_practice_areas` | List all practice areas |
| `list_activity_descriptions` | List billing activity codes |
| `who_am_i` | Check the authenticated user |

## Prerequisites

- Python 3.11+
- A [Clio Developer](https://developers.clio.com/) account with an app configured
- Your Clio app's **Client ID** and **Client Secret**

## Setup

1. **Clone the repository:**

   ```bash
   git clone https://github.com/YOUR_USERNAME/clio-mcp.git
   cd clio-mcp
   ```

2. **Create a virtual environment and install dependencies:**

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Configure your Clio credentials:**

   ```bash
   cp .env.example .env
   ```

   Edit `.env` and fill in your Clio app credentials:

   ```
   CLIO_CLIENT_ID=your_client_id
   CLIO_CLIENT_SECRET=your_client_secret
   CLIO_REDIRECT_URI=http://127.0.0.1
   ```

4. **Register a redirect URI in the Clio Developer Portal:**

   In your Clio app settings, add `http://127.0.0.1:8080` as a redirect URI.

## Usage with Claude Code

Add the server to your Claude Code MCP configuration (`~/.claude/settings.json` or project-level `.claude/settings.json`):

```json
{
  "mcpServers": {
    "clio": {
      "command": "/path/to/clio-mcp/.venv/bin/python",
      "args": ["/path/to/clio-mcp/clio_mcp.py"]
    }
  }
}
```

Replace `/path/to/clio-mcp` with the actual path to your clone.

## Authentication

On first use, the server will open your browser for Clio OAuth authorization. After authorizing, tokens are cached locally in `.clio_tokens.json` and automatically refreshed when they expire.

## Security Notes

- **Never commit `.env` or `.clio_tokens.json`** — they contain your credentials and are excluded by `.gitignore`.
- If you suspect your credentials have been exposed, regenerate your client secret in the [Clio Developer Portal](https://developers.clio.com/).
- This server is designed for local, single-user use. It is not intended to be deployed as a shared service.

## License

MIT
