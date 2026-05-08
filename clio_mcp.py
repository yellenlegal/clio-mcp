#!/usr/bin/env python3
"""Clio MCP Server — exposes Clio API tools to Claude Code via stdio."""

import json
import os
import sys
import threading
import time
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ENV_PATH = Path(__file__).parent / ".env"
TOKEN_PATH = Path(__file__).parent / ".clio_tokens.json"

load_dotenv(ENV_PATH)

CLIENT_ID = os.environ["CLIO_CLIENT_ID"]
CLIENT_SECRET = os.environ["CLIO_CLIENT_SECRET"]
REDIRECT_URI = os.environ.get("CLIO_REDIRECT_URI", "http://127.0.0.1")
BASE_URL = "https://app.clio.com"
API_URL = f"{BASE_URL}/api/v4"

# ---------------------------------------------------------------------------
# OAuth helpers
# ---------------------------------------------------------------------------

def _save_tokens(tokens: dict):
    TOKEN_PATH.write_text(json.dumps(tokens, indent=2))


def _load_tokens() -> dict | None:
    if TOKEN_PATH.exists():
        return json.loads(TOKEN_PATH.read_text())
    return None


def _refresh_access_token(refresh_token: str) -> dict:
    resp = httpx.post(
        f"{BASE_URL}/oauth/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
    )
    resp.raise_for_status()
    tokens = resp.json()
    _save_tokens(tokens)
    return tokens


def _exchange_code(code: str) -> dict:
    resp = httpx.post(
        f"{BASE_URL}/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "redirect_uri": REDIRECT_URI,
        },
    )
    resp.raise_for_status()
    tokens = resp.json()
    _save_tokens(tokens)
    return tokens


class _OAuthCallbackHandler(BaseHTTPRequestHandler):
    """Tiny HTTP handler that captures the OAuth callback code."""
    code: str | None = None

    def do_GET(self):
        qs = parse_qs(urlparse(self.path).query)
        _OAuthCallbackHandler.code = qs.get("code", [None])[0]
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<h2>Authorized! You can close this tab.</h2>")

    def log_message(self, *args):
        pass  # suppress noisy logs


def _do_oauth_flow() -> dict:
    """Open browser for user authorization, capture the code, exchange it."""
    server = HTTPServer(("127.0.0.1", 8080), _OAuthCallbackHandler)
    redirect_with_port = "http://127.0.0.1:8080"

    auth_url = (
        f"{BASE_URL}/oauth/authorize"
        f"?response_type=code"
        f"&client_id={CLIENT_ID}"
        f"&redirect_uri={redirect_with_port}"
    )

    # Print to stderr so it doesn't interfere with MCP stdio protocol
    print(f"\n>>> Opening browser for Clio authorization...", file=sys.stderr)
    print(f">>> If it doesn't open, visit:\n{auth_url}\n", file=sys.stderr)
    webbrowser.open(auth_url)

    # Wait for callback
    _OAuthCallbackHandler.code = None
    while _OAuthCallbackHandler.code is None:
        server.handle_request()

    server.server_close()

    # Exchange code — use the port-based redirect URI that matches
    resp = httpx.post(
        f"{BASE_URL}/oauth/token",
        data={
            "grant_type": "authorization_code",
            "code": _OAuthCallbackHandler.code,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
            "redirect_uri": redirect_with_port,
        },
    )
    resp.raise_for_status()
    tokens = resp.json()
    _save_tokens(tokens)
    return tokens


def _get_access_token() -> str:
    """Return a valid access token, refreshing or re-authorizing as needed."""
    tokens = _load_tokens()

    if tokens and tokens.get("access_token"):
        # Try a quick test call
        test = httpx.get(
            f"{API_URL}/users/who_am_i",
            headers={"Authorization": f"Bearer {tokens['access_token']}"},
        )
        if test.status_code == 200:
            return tokens["access_token"]

        # Try refresh
        if tokens.get("refresh_token"):
            try:
                tokens = _refresh_access_token(tokens["refresh_token"])
                return tokens["access_token"]
            except Exception:
                pass

    # Full OAuth flow needed
    tokens = _do_oauth_flow()
    return tokens["access_token"]


def _api(method: str, path: str, **kwargs) -> dict:
    """Make an authenticated Clio API call."""
    token = _get_access_token()
    resp = httpx.request(
        method,
        f"{API_URL}{path}",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        **kwargs,
    )
    if not resp.is_success:
        raise Exception(f"{resp.status_code} {resp.reason_phrase}: {resp.text}")
    return resp.json()


# ---------------------------------------------------------------------------
# Cached current user
# ---------------------------------------------------------------------------

_current_user: dict | None = None


def _get_current_user() -> dict:
    """Return the authenticated Clio user (cached after first call)."""
    global _current_user
    if _current_user is None:
        resp = _api("GET", "/users/who_am_i.json?fields=id,name,email")
        _current_user = resp["data"]
    return _current_user


# ---------------------------------------------------------------------------
# MCP Server
# ---------------------------------------------------------------------------

mcp = FastMCP("Clio", instructions="Tools for managing Clio legal practice data.")


# ---- Time Entries ----------------------------------------------------------

@mcp.tool()
def create_time_entry(
    date: str,
    matter_id: int,
    quantity_in_seconds: int,
    note: str = "",
    activity_description_id: int | None = None,
) -> str:
    """Create a billable time entry in Clio.

    Args:
        date: Date of the work (YYYY-MM-DD)
        matter_id: Clio matter ID to bill against
        quantity_in_seconds: Duration in seconds (e.g. 3600 = 1 hour)
        note: Description of work performed
        activity_description_id: Optional Clio activity description ID
    """
    body: dict = {
        "data": {
            "type": "TimeEntry",
            "date": date,
            "quantity": quantity_in_seconds,
            "matter": {"id": matter_id},
            "note": note,
        }
    }
    if activity_description_id:
        body["data"]["activity_description"] = {"id": activity_description_id}

    result = _api(
        "POST",
        "/activities.json?fields=id,type,date,quantity,total,note,matter{id,display_number,description}",
        json=body,
    )
    return json.dumps(result, indent=2)


# ---- Hard Cost / Expense Entries -------------------------------------------

@mcp.tool()
def create_expense_entry(
    date: str,
    matter_id: int,
    total: float,
    note: str = "",
    quantity: int = 1,
) -> str:
    """Create a hard cost / expense entry in Clio.

    Args:
        date: Date of the expense (YYYY-MM-DD)
        matter_id: Clio matter ID
        total: Dollar amount of the expense
        note: Description of the expense
        quantity: Number of units (default 1)
    """
    body = {
        "data": {
            "type": "ExpenseEntry",
            "date": date,
            "quantity": quantity,
            "price": total / quantity,
            "matter": {"id": matter_id},
            "note": note,
        }
    }
    result = _api(
        "POST",
        "/activities.json?fields=id,type,date,quantity,price,total,note,matter{id,display_number,description}",
        json=body,
    )
    return json.dumps(result, indent=2)


# ---- Matters ---------------------------------------------------------------

@mcp.tool()
def create_matter(
    description: str,
    client_id: int,
    status: str = "Open",
    practice_area_id: int | None = None,
) -> str:
    """Create a new matter in Clio.

    Args:
        description: Name/description of the matter
        client_id: Clio contact ID for the client
        status: Matter status — Open or Closed (default Open)
        practice_area_id: Optional practice area ID
    """
    body: dict = {
        "data": {
            "description": description,
            "client": {"id": client_id},
            "status": status,
        }
    }
    if practice_area_id:
        body["data"]["practice_area"] = {"id": practice_area_id}

    result = _api(
        "POST",
        "/matters.json?fields=id,display_number,description,status,client{id,name},practice_area{id,name}",
        json=body,
    )
    return json.dumps(result, indent=2)


# ---- Contacts --------------------------------------------------------------

@mcp.tool()
def create_contact(
    name: str,
    type: str = "Person",
    email: str | None = None,
    phone: str | None = None,
    address: str | None = None,
) -> str:
    """Create a contact in Clio.

    Args:
        name: Full name (Person) or company name (Company)
        type: 'Person' or 'Company'
        email: Email address
        phone: Phone number
        address: Street address
    """
    if type == "Person":
        parts = name.rsplit(" ", 1)
        first = parts[0]
        last = parts[1] if len(parts) > 1 else ""
        data: dict = {"type": type, "first_name": first, "last_name": last}
    else:
        data = {"type": type, "name": name}

    if email:
        data["email_addresses"] = [{"name": "Work", "address": email, "default_email": True}]
    if phone:
        data["phone_numbers"] = [{"name": "Work", "number": phone, "default_phone_number": True}]
    if address:
        data["addresses"] = [{"name": "Work", "street": address}]

    result = _api(
        "POST",
        "/contacts.json?fields=id,name,first_name,last_name,type,email_addresses,phone_numbers",
        json={"data": data},
    )
    return json.dumps(result, indent=2)


# ---- Tasks -----------------------------------------------------------------

@mcp.tool()
def create_task(
    name: str,
    matter_id: int,
    due_at: str,
    description: str = "",
    priority: str = "Normal",
    assignee_id: int | None = None,
) -> str:
    """Create a task in Clio.

    Args:
        name: Short title of the task
        matter_id: Clio matter ID to associate the task with
        due_at: Due date (YYYY-MM-DD)
        description: Longer description of the task
        priority: 'High', 'Normal', or 'Low' (default Normal)
        assignee_id: Clio user ID to assign the task to (defaults to current user)
    """
    if assignee_id is None:
        assignee_id = _get_current_user()["id"]
    body: dict = {
        "data": {
            "name": name,
            "description": description,
            "due_at": due_at,
            "priority": priority,
            "matter": {"id": matter_id},
            "assignee": {"id": assignee_id, "type": "User"},
        }
    }

    result = _api(
        "POST",
        "/tasks.json?fields=id,name,description,status,priority,due_at,matter{id,display_number,description},assignee{id,name}",
        json=body,
    )
    return json.dumps(result, indent=2)


# ---- Calendar Entries ------------------------------------------------------

_CAL_FIELDS = "id,summary,description,start_at,end_at,location,all_day,matter{id,display_number},calendar_owner{id,name}"


@mcp.tool()
def list_calendar_entries(
    matter_id: int | None = None,
    query: str | None = None,
    limit: int = 20,
) -> str:
    """List calendar entries in Clio, optionally filtered by matter.

    Args:
        matter_id: Filter to entries linked to this matter ID
        query: Optional text search on summary/description
        limit: Max entries to return (default 20)
    """
    params = f"fields={_CAL_FIELDS}&limit={limit}"
    if matter_id:
        params += f"&matter_id={matter_id}"
    if query:
        params += f"&query={query}"

    result = _api("GET", f"/calendar_entries.json?{params}")
    return json.dumps(result, indent=2)


@mcp.tool()
def create_calendar_entry(
    summary: str,
    matter_id: int,
    start_at: str,
    end_at: str,
    description: str = "",
    location: str = "",
    all_day: bool = False,
    calendar_owner_id: int | None = None,
) -> str:
    """Create a calendar entry in Clio.

    Args:
        summary: Title of the event
        matter_id: Clio matter ID to associate with
        start_at: Start datetime (ISO 8601, e.g. '2026-06-01T14:00:00-04:00') or date for all-day events ('2026-06-01T00:00:00-04:00')
        end_at: End datetime (ISO 8601) or next day for all-day events
        description: Longer description or notes
        location: Location of the event
        all_day: Whether this is an all-day event (default False)
        calendar_owner_id: Clio user ID who owns the calendar (defaults to current user)
    """
    if calendar_owner_id is None:
        calendar_owner_id = _get_current_user()["id"]
    body: dict = {
        "data": {
            "summary": summary,
            "description": description,
            "start_at": start_at,
            "end_at": end_at,
            "location": location,
            "all_day": all_day,
            "matter": {"id": matter_id},
            "calendar_owner": {"id": calendar_owner_id},
        }
    }

    result = _api("POST", f"/calendar_entries.json?fields={_CAL_FIELDS}", json=body)
    return json.dumps(result, indent=2)


@mcp.tool()
def update_calendar_entry(
    calendar_entry_id: str,
    summary: str | None = None,
    start_at: str | None = None,
    end_at: str | None = None,
    description: str | None = None,
    location: str | None = None,
    all_day: bool | None = None,
) -> str:
    """Update an existing calendar entry in Clio.

    Args:
        calendar_entry_id: The Clio calendar entry ID to update
        summary: New title (omit to keep unchanged)
        start_at: New start datetime in ISO 8601 (omit to keep unchanged)
        end_at: New end datetime in ISO 8601 (omit to keep unchanged)
        description: New description (omit to keep unchanged)
        location: New location (omit to keep unchanged)
        all_day: Whether this is an all-day event (omit to keep unchanged)
    """
    data: dict = {}
    if summary is not None:
        data["summary"] = summary
    if start_at is not None:
        data["start_at"] = start_at
    if end_at is not None:
        data["end_at"] = end_at
    if description is not None:
        data["description"] = description
    if location is not None:
        data["location"] = location
    if all_day is not None:
        data["all_day"] = all_day

    result = _api(
        "PATCH",
        f"/calendar_entries/{calendar_entry_id}.json?fields={_CAL_FIELDS}",
        json={"data": data},
    )
    return json.dumps(result, indent=2)


# ---- Lookup helpers --------------------------------------------------------

@mcp.tool()
def search_contacts(query: str) -> str:
    """Search for contacts by name. Useful for finding client IDs.

    Args:
        query: Name or partial name to search for
    """
    result = _api(
        "GET",
        f"/contacts.json?query={query}&fields=id,name,type,email_addresses&limit=10",
    )
    return json.dumps(result, indent=2)


@mcp.tool()
def search_matters(query: str) -> str:
    """Search for matters by description/number. Useful for finding matter IDs.

    Args:
        query: Description or matter number to search for
    """
    result = _api(
        "GET",
        f"/matters.json?query={query}&fields=id,display_number,description,status,client{{id,name}}&limit=10",
    )
    return json.dumps(result, indent=2)


@mcp.tool()
def list_practice_areas() -> str:
    """List all practice areas. Useful when creating matters."""
    result = _api("GET", "/practice_areas.json?fields=id,name&limit=100")
    return json.dumps(result, indent=2)


@mcp.tool()
def list_activity_descriptions() -> str:
    """List activity descriptions (billing codes). Useful when creating time entries."""
    result = _api("GET", "/activity_descriptions.json?fields=id,name,type&limit=100")
    return json.dumps(result, indent=2)


@mcp.tool()
def who_am_i() -> str:
    """Check the current authenticated Clio user."""
    result = _api("GET", "/users/who_am_i.json?fields=id,name,email")
    return json.dumps(result, indent=2)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    mcp.run(transport="stdio")
