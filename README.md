# Hermes Dashboard Ticket Deck

Read-only Hermes Dashboard plugin for Zammad ticket visibility — counts, queues, and read-only ticket detail without exposing the full Zammad UI.

> **Early / community package.** Ticket Deck is functional and tested, but the Hermes dashboard plugin API surface may change before a 1.0 release. Pin your version if you need stability.

> ⚠️ **This plugin serves ticket content, including article text that may contain customer PII.** It is read-only, but read access still matters. Review the security section before using it with real tickets.

## Why this exists

Zammad is the operational source of truth for many support workflows, but opening the full ticketing UI is not always the right shape for an operator cockpit, agent review, or lightweight Mission Control surface.

**Ticket visibility needs a narrow dashboard lane.** Ticket Deck gives Hermes operators a quick view of ticket load and ticket context:

```text
Zammad REST API → Ticket Deck tab → counts · queues · read-only detail
```

**Read-only should mean read-only.** This plugin does not create tickets, update tickets, post notes, download attachments, trigger agents, or call arbitrary URLs. It reads from Zammad with a server-side token, returns a fixed allow-list of fields, and renders ticket/article text as React text nodes rather than raw HTML.

**The full ticketing system remains the system of record.** Ticket Deck is a cockpit window, not a replacement for Zammad permissions, workflow, audit controls, or technician tooling. Any authenticated dashboard user sees what the configured Zammad token can see, so scope that token deliberately.

## Install

Copy the plugin directory into your Hermes plugins path, enable it, and restart the dashboard:

```bash
cp -r hermes-ticket-deck ~/.hermes/plugins/
hermes plugins enable hermes-ticket-deck
hermes dashboard --host 127.0.0.1 --port 9119 --no-open
```

Manual config fallback:

```yaml
plugins:
  enabled:
    - hermes-ticket-deck
```

Then restart the dashboard, or rescan if it is already running:

```bash
curl http://127.0.0.1:9119/api/dashboard/plugins/rescan
```

The **Tickets** tab appears in the dashboard navigation.

## Zammad setup

Ticket Deck needs a reachable Zammad instance and a Zammad API token. A read-only or least-privilege token is strongly recommended.

| Env var | Required | Default | Purpose |
|---|---:|---|---|
| `ZAMMAD_BASE_URL` | yes | — | Your Zammad base URL. Use `https://` for anything non-local. |
| `ZAMMAD_API_TOKEN` | yes* | — | Zammad API token. Keep it server-side. |
| `ZAMMAD_ENV_FILE` | no | `$HERMES_HOME/zammad.env` | `KEY=VALUE` file that can hold Zammad settings. |
| `ZAMMAD_UI_URL` | no | — | If set, shows an “Open full Zammad UI” link. |
| `ZAMMAD_MOCK` | no | — | `1` serves canned fixtures for demos/tests; no network. |

\* `ZAMMAD_API_TOKEN` and `ZAMMAD_BASE_URL` may live in the env file instead of the process environment. Example `zammad.env`:

```bash
ZAMMAD_BASE_URL=https://zammad.example.com
ZAMMAD_API_TOKEN=your-read-only-token
```

Create the token in Zammad under **Profile → Token Access** with the minimum rights needed to read tickets. Keep the env file out of version control; this repo's `.gitignore` excludes `*.env` and `zammad.env`.

Try it with no Zammad:

```bash
ZAMMAD_MOCK=1 hermes dashboard --host 127.0.0.1 --port 9119 --no-open
```

## Features

| Area | What it does |
|---|---|
| Tickets tab | Adds a dashboard tab for Zammad ticket visibility |
| Summary metrics | Open, unassigned, escalated, new, and high-priority visible counts |
| Queues | Open-by-group breakdown; click a group to filter |
| Presets | Open, new, pending, and high-priority views |
| Read-only detail | Shows sanitized article text, newest last, capped by count and length |
| Attachment handling | Counts attachments but never downloads or serves them |
| Mock mode | Runs canned fixtures with `ZAMMAD_MOCK=1` for demos and tests |
| Frontend | Dark-mode first, light fallback, no build step |

## API

Mounted by Hermes at:

```text
/api/plugins/hermes-ticket-deck
```

| Method | Path | Purpose |
|---|---|---|
| GET | `/health` | Zammad reachability, mode, authenticated Zammad user metadata; no secrets |
| GET | `/summary` | Capped counts and open-by-group / by-priority facets |
| GET | `/tickets` | Sanitized ticket metadata; supports `preset`, `group`, and `limit` |
| GET | `/tickets/{id}` | One ticket plus sanitized article text |

Counts are intentionally capped and reflect what the configured Zammad token can see. They are dashboard visibility signals, not global reporting truth.

## ⚠️ Read-only safety posture

Ticket Deck is intentionally read-only.

- Only `GET` routes exist.
- There are no create, update, delete, edit, import, export, post, note, attachment-download, or dispatch routes.
- The Zammad API token stays server-side and is never returned to the browser.
- `/health` reports `credential_exposed_to_client: false`.
- The backend uses a fixed allow-list of ticket and article fields.
- Article HTML is stripped and rendered as React text nodes; raw HTML and active content are not rendered.
- Query presets are allow-listed, group filters are validated, and ticket ids are integer-bounded.
- Attachments are counted but not downloaded.
- The frontend only calls its own same-origin plugin API.
- The backend refuses to send the Zammad token over `http://` to non-local hosts. Use `https://` for live Zammad.

This plugin relies on the **Hermes Dashboard auth gate** for dashboard/API protection. In Hermes Agent ≥ 0.18.0, auth is mandatory on non-loopback binds and fails closed if no auth provider is configured.

Deployment guidance:

- Use **Nous Portal OAuth** for internet exposure.
- Use username/password or self-hosted OIDC for trusted LAN / VPN deployments.
- Never use `--insecure` for anything reachable off the box.
- Do not front a loopback Hermes bind with an unauthenticated reverse proxy. If Hermes binds to `127.0.0.1`, its own non-loopback auth gate is not engaged; either put auth on the proxy or bind non-loopback so Hermes' gate engages.
- Scope the Zammad token deliberately. Any authenticated dashboard user sees every ticket the configured token can see.

## Known limitations

- This is a ticket visibility plugin, not a ticketing client.
- Counts are capped and token-visible, not authoritative global counts.
- There is no per-user Zammad authorization inside the plugin; authorization is the dashboard auth gate plus the configured Zammad token scope.
- Attachments are not previewed or downloaded.
- Article rendering is intentionally plain text after HTML stripping, not a full email/thread renderer.
- Zammad API shape and permissions can vary by version and tenant configuration.
- There is no built-in authentication layer beyond your Hermes dashboard deployment.

## Tests

```bash
./tests/run_tests.sh
```

The runner auto-selects a Python interpreter that has the dashboard test dependencies. It checks `$PYTHON`, an active `$VIRTUAL_ENV`, `/opt/hermes/.venv/bin/python`, then `python3` / `python`.

To force the Hermes venv:

```bash
PYTHON=/opt/hermes/.venv/bin/python ./tests/run_tests.sh
```

The suite runs `py_compile`, `node --check` if available, and a FastAPI `TestClient` smoke test in mock mode covering health, summary, ticket list/detail, filters, presets, ticket-id validation, HTML stripping, output sanitization, no token leakage, and the non-local `http://` token refusal guard.

## Discussions

Questions, deployment notes, and feature requests are welcome in this repo's GitHub Discussions. Keep real ticket contents, tokens, logs, and customer data out of public discussion threads.

## Credits

Built by [Claude](https://claude.ai) (Anthropic) under the direction of **Ori8**, the Hermes-based AI agent at the core of [Ori8 Automations](https://github.com/Ori8-Automations). A human provided requirements, review, and final approval.

## License

Released under the MIT License. See [LICENSE](LICENSE).
