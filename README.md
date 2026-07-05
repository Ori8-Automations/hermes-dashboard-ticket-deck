# Hermes Ticket Deck

A **read-only** [Hermes Agent](https://hermes-agent.nousresearch.com) Dashboard
plugin for [Zammad](https://zammad.org) ticketing. It gives operators an
in-dashboard view of ticket **counts, queues, and read-only ticket detail**
without exposing the full Zammad UI — useful when you want visibility (e.g.
alongside agent/automation work) but not another login surface or write access.

> **Early / community package.** This is a small public-safe dashboard utility,
> not an official Zammad client and not a replacement for Zammad permissions,
> workflow, or audit controls. Review the security section before using it with
> real tickets.

```
Zammad REST API  →  Ticket Deck tab  →  counts · queues · read-only detail
```

> ⚠️ **This plugin serves ticket content (including article text, which may
> contain customer PII).** Read the [Security & exposure](#security--exposure)
> section before deploying. It is read-only, but read access still matters.

<!-- Add a screenshot here once deployed, e.g.:
![Ticket Deck](docs/screenshot.png)
-->

## Features

- **Tickets tab** in the Hermes Dashboard.
- **Summary metrics**: open / unassigned / escalated / high-priority counts
  (capped, reflecting what the configured token can see).
- **Queues**: open-by-group breakdown; click a group to filter.
- **Presets**: open / new / pending / high.
- **Read-only ticket detail**: sanitized article text, newest last, capped in
  count and length. Attachments are counted but never downloaded.
- **Mock mode** (`ZAMMAD_MOCK=1`): canned tickets so you can try it (and run the
  tests) with no live Zammad.
- **Dark-mode first**, with a light fallback.
- **Read-only and safe**: the Zammad token stays server-side and is never sent
  to the browser; there are no write/delete/edit routes and no raw HTML.

## Why this exists

Hermes operators often need ticket context while reviewing agent work, triage
queues, or operational reports. Opening the full ticketing system is sometimes
too much surface area for a lightweight cockpit view. Ticket Deck gives a narrow,
read-only window into Zammad: enough context to decide what needs attention,
without exposing write buttons or downloading attachments.

## Requirements

- **Hermes Agent ≥ 0.18.0** (for the mandatory dashboard auth gate — see below).
- A reachable Zammad instance and an API token.
- No build step, no npm dependencies (frontend uses the Hermes Plugin SDK); no
  extra Python dependencies beyond what Hermes ships (FastAPI).

## Install

```bash
cp -r hermes-ticket-deck ~/.hermes/plugins/
hermes plugins enable hermes-ticket-deck
hermes dashboard --host 127.0.0.1 --port 9119 --no-open
```

> Copy the repo directory (the one with `plugin.yaml` and `dashboard/`) to
> `~/.hermes/plugins/hermes-ticket-deck`. The `tests/` folder is harmless.

**Manual config fallback** (if you manage plugins via config):

```yaml
plugins:
  enabled:
    - hermes-ticket-deck
```

then restart the dashboard (or `curl http://127.0.0.1:9119/api/dashboard/plugins/rescan`).

## Configuration

| Env var | Required | Default | Purpose |
|---|---|---|---|
| `ZAMMAD_BASE_URL` | yes | — | Your Zammad base URL. **Use `https://`.** |
| `ZAMMAD_API_TOKEN` | yes* | — | Zammad API token (read-only token recommended). |
| `ZAMMAD_ENV_FILE` | no | `$HERMES_HOME/zammad.env` | `KEY=VALUE` file that can hold the two above. |
| `ZAMMAD_UI_URL` | no | — | If set, shows an "Open full Zammad UI" link. |
| `ZAMMAD_MOCK` | no | — | `1` serves canned fixtures (demo/tests); no network. |

\* `ZAMMAD_API_TOKEN` / `ZAMMAD_BASE_URL` may live in the env file instead of the
process environment. Example `zammad.env`:

```bash
ZAMMAD_BASE_URL=https://zammad.example.com
ZAMMAD_API_TOKEN=your-read-only-token
```

Create the token in Zammad under **Profile → Token Access** with the minimum
rights needed to read tickets. Keep the env file out of version control
(this repo's `.gitignore` already excludes `*.env`).

**Try it with no Zammad:**

```bash
ZAMMAD_MOCK=1 hermes dashboard --port 9119 --no-open
```

## Security & exposure

This plugin exposes ticket data over `/api/plugins/hermes-ticket-deck/*`. Those
routes are protected by the **Hermes Dashboard auth gate**, which is
**mandatory on any non-loopback bind in Hermes ≥ 0.18.0** and fails closed if no
auth provider is configured. To deploy safely:

- **Pick an auth provider** (Hermes ≥ 0.18.0):
  - **Nous Portal OAuth** — for exposure to the public internet.
  - **Username/password** or **self-hosted OIDC** — for trusted LAN / VPN.
- **Never use `--insecure`** for anything reachable off the box.
- **Don't front a loopback bind with an unauthenticated reverse proxy.** If
  Hermes binds to `127.0.0.1`, its gate stays off; either put auth on the proxy
  or bind non-loopback so Hermes' own gate engages.
- **Prefer `https` for `ZAMMAD_BASE_URL`.** The token is sent in an
  `Authorization` header on every call. This plugin **refuses to send the token
  over `http://` to a non-local host** (localhost/tunnel endpoints are allowed).
- **Automation / service-to-service**: use a dashboard auth provider that sets
  `supports_token = True` and call with an authenticated bearer token.

What the plugin itself guarantees:

- **Token stays server-side** — never returned to the client (`/health` reports
  `credential_exposed_to_client: false`).
- **Read-only** — only `GET` routes; no create/update/delete anywhere.
- **Sanitized output** — a fixed allow-list of fields; article HTML is stripped
  and rendered as React text nodes (no raw HTML / active content); result counts
  and article bodies are capped.
- **Validated input** — query filters must match a strict pattern, presets are
  an allow-list, ticket ids are integer-bounded.
- **No attachment downloads**, no arbitrary URL fetches, no WebSockets.

> Note: the auth gate authenticates *a* user; it is not per-user authorization.
> Any authenticated dashboard user sees every ticket the configured token can.
> Scope the Zammad token accordingly.

## API routes (mounted at `/api/plugins/hermes-ticket-deck`)

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | API reachability + mode; no secrets |
| GET | `/summary` | Capped counts and open-by-group / by-priority facets |
| GET | `/tickets` | Sanitized ticket metadata (`preset`, `group`, `limit`) |
| GET | `/tickets/{id}` | One ticket + sanitized article text (read-only) |

## Tests

```bash
./tests/run_tests.sh
```

Runs `py_compile`, `node --check` (if node is present), and a FastAPI
`TestClient` smoke suite in **mock mode** — covering health/summary/tickets/
detail, filter + preset + ticket-id validation, HTML stripping, output
sanitization (no token or unexpected fields leak), and the `http`-token refusal.

The runner auto-selects a Python that has the test deps: it prefers `$PYTHON`,
then an active `$VIRTUAL_ENV`, then `/opt/hermes/.venv/bin/python`, then
`python3`. The system `python3` usually lacks `fastapi`, so point at the venv if
needed:

```bash
PYTHON=/opt/hermes/.venv/bin/python ./tests/run_tests.sh
```

## Credits

Built with Claude Code and Ori8, with human review before publication.

## License

Released under the MIT License. See [LICENSE](LICENSE).
