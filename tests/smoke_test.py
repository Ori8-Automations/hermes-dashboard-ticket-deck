#!/usr/bin/env python3
"""Self-contained smoke test for the Hermes Ticket Deck backend.

Runs the FastAPI router in ZAMMAD_MOCK=1 mode (no network) and asserts the
read-only API behaves and never leaks the token or unexpected fields. Also
directly checks the http-token refusal guard.

Usage:
    pip install fastapi httpx
    python tests/smoke_test.py
"""

import importlib
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
DASH = HERE.parent / "dashboard"

PASSED = 0


def ok(cond, msg):
    global PASSED
    print(("PASS" if cond else "FAIL"), msg)
    if not cond:
        raise AssertionError(msg)
    PASSED += 1


def main() -> int:
    os.environ["ZAMMAD_MOCK"] = "1"
    os.environ.pop("ZAMMAD_BASE_URL", None)
    os.environ.pop("ZAMMAD_API_TOKEN", None)
    sys.path.insert(0, str(DASH))

    import plugin_api  # noqa: E402
    importlib.reload(plugin_api)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(plugin_api.router, prefix="/api/plugins/hermes-ticket-deck")
    c = TestClient(app)
    B = "/api/plugins/hermes-ticket-deck"

    # --- health ---
    hz = c.get(B + "/health").json()
    ok(hz["status"] == "ok" and hz["mode"] == "mock", "health: mock mode ok")
    ok(hz["credential_exposed_to_client"] is False, "health: credential flag false")

    # --- summary ---
    sm = c.get(B + "/summary").json()
    ok(sm["counts"]["open_visible_capped"] == 1, "summary: open count")
    ok(sm["counts"]["open_unassigned_visible_capped"] == 1, "summary: unassigned count")
    ok(sm["counts"]["open_escalated_visible_capped"] == 1, "summary: escalated count")
    ok(sm["counts"]["high_priority_visible_capped"] == 1, "summary: high-priority count")
    ok("Support" in sm["facets"]["open_by_group"], "summary: group facet")

    # --- tickets list + presets + group filter ---
    tk = c.get(B + "/tickets?preset=open").json()
    ok(tk["count"] == 1 and tk["tickets"][0]["number"] == "20260701", "tickets: open preset")
    ok(tk["tickets"][0]["unassigned"] is True, "tickets: unassigned flag")
    ok(c.get(B + "/tickets?preset=new").json()["count"] == 1, "tickets: new preset")
    ok(c.get(B + "/tickets?preset=pending").json()["count"] == 1, "tickets: pending preset")
    ok(c.get(B + "/tickets?preset=high").json()["count"] == 1, "tickets: high preset")
    ok(c.get(B + "/tickets?preset=open&group=Support").json()["count"] == 1, "tickets: group filter match")
    ok(c.get(B + "/tickets?preset=open&group=Product").json()["count"] == 0, "tickets: group filter no-match")

    # list output must not leak raw/internal fields
    row = tk["tickets"][0]
    ok("state_id" not in row and "owner" not in row, "tickets: sanitized (no raw ids leak beyond owner_id)")

    # --- validation ---
    ok(c.get(B + "/tickets?preset=bogus").status_code == 400, "validation: unknown preset 400")
    ok(c.get(B + "/tickets?group=%22evil%22").status_code == 400, "validation: bad group (quote) 400")
    ok(c.get(B + "/tickets/abc").status_code == 422, "validation: non-int ticket id 422")
    ok(c.get(B + "/tickets/0").status_code == 400, "validation: ticket id 0 rejected")
    ok(c.get(B + "/tickets/999").status_code == 404, "validation: missing ticket 404")

    # --- detail: sanitized article text + HTML strip + ordering ---
    detail = c.get(B + "/tickets/101").json()
    ok(detail["ticket"]["number"] == "20260701", "detail: ticket meta")
    arts = detail["articles"]
    ok(len(arts) == 3, "detail: article count")
    ok(all("<" not in a["body"] for a in arts), "detail: HTML stripped from bodies")
    ok(arts[0]["created_at"] <= arts[-1]["created_at"], "detail: articles sorted oldest→newest")
    ok(arts[0]["body"] == "I get a 500 on login.", "detail: html body sanitized to text")
    ok(any(a["attachment_count"] == 1 for a in arts), "detail: attachment counted, not downloaded")
    ok(detail["limits"]["attachments_included"] is False, "detail: attachments not included")

    # --- token never appears anywhere in responses ---
    os.environ["ZAMMAD_API_TOKEN"] = "SUPERSECRET-TOKEN-XYZ"
    importlib.reload(plugin_api)
    app2 = FastAPI(); app2.include_router(plugin_api.router, prefix=B)
    c2 = TestClient(app2)
    blob = (c2.get(B + "/health").text + c2.get(B + "/summary").text
            + c2.get(B + "/tickets").text + c2.get(B + "/tickets/101").text)
    ok("SUPERSECRET" not in blob, "security: token never appears in any response")

    # --- http-token refusal guard (live path, mock off) ---
    os.environ["ZAMMAD_MOCK"] = ""
    os.environ["ZAMMAD_BASE_URL"] = "http://zammad.example.com"  # non-local http
    importlib.reload(plugin_api)
    app3 = FastAPI(); app3.include_router(plugin_api.router, prefix=B)
    c3 = TestClient(app3)
    r = c3.get(B + "/summary")
    ok(r.status_code == 503 and "http" in r.json()["detail"].lower(), "security: refuses token over http to remote host")
    # localhost http is allowed through the guard (will then fail to connect, 502)
    os.environ["ZAMMAD_BASE_URL"] = "http://127.0.0.1:9"
    importlib.reload(plugin_api)
    app4 = FastAPI(); app4.include_router(plugin_api.router, prefix=B)
    c4 = TestClient(app4)
    ok(c4.get(B + "/summary").status_code == 502, "security: localhost http passes guard (then upstream 502)")

    print(f"\nALL {PASSED} SMOKE TESTS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
