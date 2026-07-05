"""Hermes Ticket Deck — read-only Zammad ticketing dashboard (backend).

Mounted by the Hermes Dashboard at /api/plugins/hermes-ticket-deck/ when
installed. It talks to a Zammad instance's REST API with a server-side token and
exposes read-only, sanitized ticket metadata:

- GET /health
- GET /summary
- GET /tickets
- GET /tickets/{ticket_id}

There are no write actions, attachment downloads, credential exposure, external
writeback, WebSocket routes, or arbitrary URL fetches. The Zammad token stays on
the server and is never returned to the client.

Configuration (environment variables):
- ZAMMAD_BASE_URL   Base URL of your Zammad instance (https recommended).
- ZAMMAD_API_TOKEN  API token (or put it in the env file below).
- ZAMMAD_ENV_FILE   Optional path to a KEY=VALUE file holding the above
                    (default: $HERMES_HOME/zammad.env).
- ZAMMAD_UI_URL     Optional URL for the "Open full Zammad UI" link (frontend
                    reads it from /health; omitted if unset).
- ZAMMAD_MOCK=1     Serve canned fixtures instead of calling Zammad (demo/tests).

Auth note: these routes serve ticket content. Their protection depends on the
Hermes Dashboard auth gate (mandatory on non-loopback binds in Hermes >= 0.18.0).
See the README.
"""
from __future__ import annotations

import datetime as dt
import html
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Optional

try:
    from fastapi import APIRouter, HTTPException, Query
except Exception:  # Allows import/compile without dashboard deps present.
    class HTTPException(Exception):  # type: ignore[no-redef]
        def __init__(self, status_code: int, detail: str = "") -> None:
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    class Query:  # type: ignore[no-redef]
        def __init__(self, default: Any = None, **_kwargs: Any) -> None:
            self.default = default

    class APIRouter:  # type: ignore[no-redef]
        def get(self, *_args: Any, **_kwargs: Any):
            return lambda fn: fn


router = APIRouter()

_HERMES_HOME = Path(os.environ.get("HERMES_HOME", str(Path.home() / ".hermes")))
ENV_PATH = Path(os.getenv("ZAMMAD_ENV_FILE", str(_HERMES_HOME / "zammad.env")))

DEFAULT_LIMIT = 25
MAX_LIMIT = 100
MAX_DETAIL_ARTICLES = 25
MAX_ARTICLE_BODY_CHARS = 8000
CATALOG_TTL_SECONDS = 300

FILTER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._: /-]{0,80}$")
SAFE_QUERY_PRESETS = {
    "open": "state.name:open",
    "new": "state.name:new",
    "pending": "state.name:pending*",
    "high": "priority.name:\"3 high\"",
}

_MOCK = os.getenv("ZAMMAD_MOCK", "").strip().lower() in {"1", "true", "yes", "on"}

# Simple per-process TTL cache for the (rarely-changing) catalogs.
_catalog_cache: dict[str, Any] = {"at": 0.0, "data": None}


def _http_error(status: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status, detail=detail)


def _iso_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------- #
# Settings & upstream access
# --------------------------------------------------------------------------- #


def _load_env() -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        text = ENV_PATH.read_text(encoding="utf-8", errors="replace")
    except FileNotFoundError:
        return values
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def _settings() -> tuple[str, str]:
    env_file = _load_env()
    token = os.getenv("ZAMMAD_API_TOKEN") or env_file.get("ZAMMAD_API_TOKEN")
    base_url = os.getenv("ZAMMAD_BASE_URL") or env_file.get("ZAMMAD_BASE_URL")
    if not base_url:
        raise _http_error(503, "zammad base url not configured")
    base_url = str(base_url).strip().rstrip("/")
    if not token:
        raise _http_error(503, "zammad token not configured")
    if not (base_url.startswith("http://") or base_url.startswith("https://")):
        raise _http_error(503, "zammad base url invalid")
    # Refuse to send the token in cleartext to a non-local host.
    if base_url.startswith("http://"):
        host = urllib.parse.urlparse(base_url).hostname or ""
        if host not in {"127.0.0.1", "::1", "localhost"}:
            raise _http_error(
                503,
                "refusing to send token over http to a non-local host; use https "
                "(or set ZAMMAD_BASE_URL to a localhost/tunnel endpoint)",
            )
    return base_url, token


def _ui_url() -> Optional[str]:
    url = (os.getenv("ZAMMAD_UI_URL") or _load_env().get("ZAMMAD_UI_URL") or "").strip()
    if url and (url.startswith("http://") or url.startswith("https://")):
        return url
    return None


def _zammad_get(path: str, params: Optional[dict[str, Any]] = None) -> Any:
    base_url, token = _settings()
    query = ""
    if params:
        query = "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    request = urllib.request.Request(
        base_url + path + query,
        headers={
            "Authorization": "Token token=" + token,
            "Accept": "application/json",
            "User-Agent": "hermes-ticket-deck/1.0",
        },
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=20) as response:  # noqa: S310 (scheme validated above)
            raw = response.read().decode("utf-8", errors="replace")
            return json.loads(raw) if raw.strip() else None
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            raise _http_error(502, "zammad api auth failed")
        raise _http_error(502, f"zammad api returned {exc.code}")
    except Exception:
        raise _http_error(502, "zammad api unavailable")


# --------------------------------------------------------------------------- #
# Validation helpers
# --------------------------------------------------------------------------- #


def _clean_query_value(value: Any, name: str) -> Optional[str]:
    if hasattr(value, "default"):
        value = getattr(value, "default")
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if not FILTER_RE.match(text):
        raise _http_error(400, f"invalid {name}")
    return text


def _safe_limit(value: Any) -> int:
    if hasattr(value, "default"):
        value = getattr(value, "default")
    try:
        limit = int(value or DEFAULT_LIMIT)
    except Exception:
        raise _http_error(400, "invalid limit")
    return max(1, min(limit, MAX_LIMIT))


def _safe_ticket_id(value: Any) -> int:
    try:
        ticket_id = int(str(value).strip())
    except Exception:
        raise _http_error(400, "invalid ticket id")
    if ticket_id < 1 or ticket_id > 99_999_999:
        raise _http_error(400, "invalid ticket id")
    return ticket_id


# --------------------------------------------------------------------------- #
# Sanitization
# --------------------------------------------------------------------------- #


def _name_map(items: Any) -> dict[int, str]:
    mapping: dict[int, str] = {}
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and isinstance(item.get("id"), int):
                mapping[item["id"]] = str(item.get("name") or item["id"])
    return mapping


def _catalogs() -> dict[str, dict[int, str]]:
    now = time.monotonic()
    cached = _catalog_cache.get("data")
    if cached is not None and (now - _catalog_cache["at"]) < CATALOG_TTL_SECONDS:
        return cached
    data = {
        "states": _name_map(_zammad_get("/api/v1/ticket_states")),
        "groups": _name_map(_zammad_get("/api/v1/groups")),
        "priorities": _name_map(_zammad_get("/api/v1/ticket_priorities")),
    }
    _catalog_cache["at"] = now
    _catalog_cache["data"] = data
    return data


def _is_escalated(ticket: dict[str, Any]) -> bool:
    now = dt.datetime.now(dt.timezone.utc)
    for key in ("escalation_at", "first_response_escalation_at", "update_escalation_at", "close_escalation_at"):
        value = ticket.get(key)
        if not isinstance(value, str) or not value:
            continue
        try:
            stamp = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        except Exception:
            continue
        if stamp <= now:
            return True
    return False


def _public_ticket(ticket: dict[str, Any], catalogs: dict[str, dict[int, str]]) -> dict[str, Any]:
    state_id = ticket.get("state_id")
    group_id = ticket.get("group_id")
    priority_id = ticket.get("priority_id")
    owner_id = ticket.get("owner_id")
    return {
        "id": ticket.get("id"),
        "number": str(ticket.get("number") or ""),
        "title": str(ticket.get("title") or "Untitled")[:240],
        "state": catalogs["states"].get(state_id, str(state_id or "unknown")),
        "group": catalogs["groups"].get(group_id, str(group_id or "unknown")),
        "priority": catalogs["priorities"].get(priority_id, str(priority_id or "unknown")),
        "owner_id": owner_id,
        "unassigned": owner_id in (None, 1),
        "escalated": _is_escalated(ticket),
        "created_at": ticket.get("created_at"),
        "updated_at": ticket.get("updated_at"),
        "article_count": ticket.get("article_count"),
    }


def _strip_html(value: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", value)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _public_article(article: dict[str, Any]) -> dict[str, Any]:
    body = article.get("body")
    if not isinstance(body, str):
        body = ""
    content_type = str(article.get("content_type") or "")
    if "html" in content_type.lower() or "<" in body:
        body = _strip_html(body)
    body = body[:MAX_ARTICLE_BODY_CHARS]
    attachments = article.get("attachments")
    attachment_count = len(attachments) if isinstance(attachments, list) else 0
    return {
        "id": article.get("id"),
        "ticket_id": article.get("ticket_id"),
        "type": str(article.get("type") or ""),
        "sender": str(article.get("sender") or ""),
        "from": str(article.get("from") or ""),
        "to": str(article.get("to") or ""),
        "subject": str(article.get("subject") or "")[:240],
        "internal": bool(article.get("internal")),
        "created_at": article.get("created_at"),
        "updated_at": article.get("updated_at"),
        "body": body,
        "body_truncated": len(body) >= MAX_ARTICLE_BODY_CHARS,
        "attachment_count": attachment_count,
    }


def _search_tickets(query: str, limit: int = DEFAULT_LIMIT) -> list[dict[str, Any]]:
    data = _zammad_get("/api/v1/tickets/search", {"query": query, "limit": limit, "expand": "true"})
    if not isinstance(data, list):
        raise _http_error(502, "zammad ticket search returned unexpected shape")
    return [ticket for ticket in data if isinstance(ticket, dict)]


# --------------------------------------------------------------------------- #
# Mock mode (ZAMMAD_MOCK=1) — canned fixtures, no network
# --------------------------------------------------------------------------- #

_MOCK_TICKETS = [
    {
        "id": 101, "number": "20260701", "title": "Login page returns 500 after deploy",
        "state_id": 2, "group_id": 1, "priority_id": 3, "owner_id": None,
        "escalation_at": "2000-01-01T00:00:00Z", "article_count": 3,
        "created_at": "2026-07-01T08:00:00Z", "updated_at": "2026-07-01T09:30:00Z",
    },
    {
        "id": 102, "number": "20260702", "title": "Feature request: dark mode",
        "state_id": 1, "group_id": 2, "priority_id": 2, "owner_id": 5,
        "article_count": 1, "created_at": "2026-07-02T10:00:00Z", "updated_at": "2026-07-02T10:05:00Z",
    },
    {
        "id": 103, "number": "20260703", "title": "Billing question about invoice #4471",
        "state_id": 3, "group_id": 1, "priority_id": 2, "owner_id": 6,
        "article_count": 2, "created_at": "2026-07-03T11:00:00Z", "updated_at": "2026-07-03T12:00:00Z",
    },
]
_MOCK_CATALOGS = {
    "states": {1: "new", 2: "open", 3: "pending reminder"},
    "groups": {1: "Support", 2: "Product"},
    "priorities": {1: "1 low", 2: "2 normal", 3: "3 high"},
}
_MOCK_ARTICLES = {
    101: [
        {"id": 1, "ticket_id": 101, "type": "email", "sender": "Customer", "from": "user@example.com",
         "to": "support@example.com", "subject": "Login broken", "internal": False,
         "created_at": "2026-07-01T08:00:00Z", "body": "<p>I get a <b>500</b> on login.</p>",
         "content_type": "text/html", "attachments": []},
        {"id": 2, "ticket_id": 101, "type": "note", "sender": "Agent", "from": "agent",
         "subject": "", "internal": True, "created_at": "2026-07-01T08:30:00Z",
         "body": "Looks like a bad migration.", "content_type": "text/plain", "attachments": []},
        {"id": 3, "ticket_id": 101, "type": "email", "sender": "Agent", "from": "support@example.com",
         "to": "user@example.com", "subject": "Re: Login broken", "internal": False,
         "created_at": "2026-07-01T09:30:00Z", "body": "Fix is deploying now.", "content_type": "text/plain",
         "attachments": [{"id": 9, "filename": "log.txt"}]},
    ],
}


def _mock_search(preset_query: str, limit: int) -> list[dict[str, Any]]:
    if preset_query.startswith("state.name:open"):
        rows = [t for t in _MOCK_TICKETS if t["state_id"] == 2]
    elif preset_query.startswith("state.name:new"):
        rows = [t for t in _MOCK_TICKETS if t["state_id"] == 1]
    elif preset_query.startswith("state.name:pending"):
        rows = [t for t in _MOCK_TICKETS if t["state_id"] == 3]
    elif preset_query.startswith("priority.name"):
        rows = [t for t in _MOCK_TICKETS if t["priority_id"] == 3]
    else:
        rows = list(_MOCK_TICKETS)
    if "group.name:" in preset_query:
        m = re.search(r'group\.name:"([^"]+)"', preset_query)
        if m:
            gid = {v: k for k, v in _MOCK_CATALOGS["groups"].items()}.get(m.group(1))
            rows = [t for t in rows if t.get("group_id") == gid]
    return rows[:limit]


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #


@router.get("/health")
def health() -> dict[str, Any]:
    """Check API reachability without returning secret material."""
    if _MOCK:
        return {
            "schema_version": 1, "status": "ok", "checked_at": _iso_now(),
            "zammad_user": {"id": 0, "login": "mock"}, "mode": "mock",
            "ui_url": _ui_url(), "credential_exposed_to_client": False,
        }
    me = _zammad_get("/api/v1/users/me")
    return {
        "schema_version": 1, "status": "ok", "checked_at": _iso_now(),
        "zammad_user": {
            "id": me.get("id") if isinstance(me, dict) else None,
            "login": me.get("login") if isinstance(me, dict) else None,
        },
        "mode": "read-only", "ui_url": _ui_url(), "credential_exposed_to_client": False,
    }


@router.get("/summary")
def summary() -> dict[str, Any]:
    """Return aggregate ticket counts from capped read-only searches."""
    if _MOCK:
        catalogs = _MOCK_CATALOGS
        open_tickets = _mock_search(SAFE_QUERY_PRESETS["open"], MAX_LIMIT)
        new_tickets = _mock_search(SAFE_QUERY_PRESETS["new"], MAX_LIMIT)
        high_tickets = _mock_search(SAFE_QUERY_PRESETS["high"], MAX_LIMIT)
    else:
        catalogs = _catalogs()
        open_tickets = _search_tickets(SAFE_QUERY_PRESETS["open"], MAX_LIMIT)
        new_tickets = _search_tickets(SAFE_QUERY_PRESETS["new"], MAX_LIMIT)
        high_tickets = _search_tickets(SAFE_QUERY_PRESETS["high"], MAX_LIMIT)

    public_open = [_public_ticket(t, catalogs) for t in open_tickets]
    by_group: dict[str, int] = {}
    by_priority: dict[str, int] = {}
    for ticket in public_open:
        by_group[ticket["group"]] = by_group.get(ticket["group"], 0) + 1
        by_priority[ticket["priority"]] = by_priority.get(ticket["priority"], 0) + 1
    return {
        "schema_version": 1,
        "source": "zammad-mock" if _MOCK else "zammad-api-read-only",
        "checked_at": _iso_now(),
        "counts": {
            "open_visible_capped": len(open_tickets),
            "open_unassigned_visible_capped": sum(1 for t in public_open if t["unassigned"]),
            "open_escalated_visible_capped": sum(1 for t in public_open if t["escalated"]),
            "new_visible_capped": len(new_tickets),
            "high_priority_visible_capped": len(high_tickets),
        },
        "facets": {
            "open_by_group": dict(sorted(by_group.items())),
            "open_by_priority": dict(sorted(by_priority.items())),
        },
        "limits": {
            "per_query_cap": MAX_LIMIT,
            "counts_are": "visible-to-token-and-capped",
            "article_bodies_included": False,
        },
    }


@router.get("/tickets")
def tickets(
    preset: Optional[str] = Query("open", description="Preset: open, new, pending, high"),
    group: Optional[str] = Query(None, description="Optional group name filter appended to preset"),
    limit: int = Query(DEFAULT_LIMIT, ge=1, le=MAX_LIMIT),
) -> dict[str, Any]:
    """Return sanitized ticket metadata only; no articles or body text."""
    preset_text = _clean_query_value(preset, "preset") or "open"
    if preset_text not in SAFE_QUERY_PRESETS:
        raise _http_error(400, "unknown preset")
    group_text = _clean_query_value(group, "group")
    clean_limit = _safe_limit(limit)
    query = SAFE_QUERY_PRESETS[preset_text]
    if group_text:
        query = f'{query} AND group.name:"{group_text}"'

    if _MOCK:
        catalogs = _MOCK_CATALOGS
        found = _mock_search(query, clean_limit)
    else:
        catalogs = _catalogs()
        found = _search_tickets(query, clean_limit)
    rows = [_public_ticket(ticket, catalogs) for ticket in found]
    return {
        "schema_version": 1,
        "source": "zammad-mock" if _MOCK else "zammad-api-read-only",
        "preset": preset_text,
        "group": group_text,
        "count": len(rows),
        "limit": clean_limit,
        "tickets": rows,
        "rendering_contract": {
            "article_bodies_included": False,
            "raw_html_allowed": False,
            "write_actions_available": False,
        },
    }


@router.get("/tickets/{ticket_id}")
def ticket_detail(ticket_id: int) -> dict[str, Any]:
    """Return one ticket with sanitized article text; read-only, no attachments."""
    clean_id = _safe_ticket_id(ticket_id)
    if _MOCK:
        catalogs = _MOCK_CATALOGS
        ticket = next((t for t in _MOCK_TICKETS if t["id"] == clean_id), None)
        if ticket is None:
            raise _http_error(404, "ticket not found")
        articles = _MOCK_ARTICLES.get(clean_id, [])
    else:
        catalogs = _catalogs()
        ticket = _zammad_get(f"/api/v1/tickets/{clean_id}")
        if not isinstance(ticket, dict):
            raise _http_error(404, "ticket not found")
        articles = _zammad_get(f"/api/v1/ticket_articles/by_ticket/{clean_id}")
        if not isinstance(articles, list):
            articles = []

    public_articles = [_public_article(a) for a in articles if isinstance(a, dict)]
    public_articles.sort(key=lambda item: str(item.get("created_at") or ""))
    if len(public_articles) > MAX_DETAIL_ARTICLES:
        public_articles = public_articles[-MAX_DETAIL_ARTICLES:]
    return {
        "schema_version": 1,
        "source": "zammad-mock" if _MOCK else "zammad-api-read-only",
        "ticket": _public_ticket(ticket, catalogs),
        "articles": public_articles,
        "limits": {
            "max_articles": MAX_DETAIL_ARTICLES,
            "max_article_body_chars": MAX_ARTICLE_BODY_CHARS,
            "attachments_included": False,
            "write_actions_available": False,
            "raw_html_allowed": False,
        },
    }
