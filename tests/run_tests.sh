#!/usr/bin/env bash
# Static checks + API smoke test for the Hermes Ticket Deck plugin.
#
# Picks a Python that actually has the test deps (fastapi). The system python3
# usually does NOT — the Hermes dashboard runs in its own venv. Order:
#   1. $PYTHON  2. $VIRTUAL_ENV/bin/python  3. /opt/hermes/.venv/bin/python
#   4. python3 / python on PATH   (first that can import fastapi wins)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DASH="$ROOT/dashboard"

pick_python() {
  local candidates=()
  [ -n "${PYTHON:-}" ] && candidates+=("$PYTHON")
  [ -n "${VIRTUAL_ENV:-}" ] && candidates+=("$VIRTUAL_ENV/bin/python")
  candidates+=("/opt/hermes/.venv/bin/python" "python3" "python")
  for cand in "${candidates[@]}"; do
    if command -v "$cand" >/dev/null 2>&1 && "$cand" -c "import fastapi" >/dev/null 2>&1; then
      echo "$cand"; return 0
    fi
  done
  return 1
}

if ! PY="$(pick_python)"; then
  echo "ERROR: no Python with 'fastapi' found." >&2
  echo "  PYTHON=/opt/hermes/.venv/bin/python ./tests/run_tests.sh   (or: pip install fastapi httpx)" >&2
  exit 1
fi
echo "Using Python: $PY"

echo "== py_compile =="
"$PY" -m py_compile "$DASH/plugin_api.py"
echo "PASS python compile"

echo "== node --check =="
if command -v node >/dev/null 2>&1; then
  node --check "$DASH/dist/index.js"
  echo "PASS js syntax"
else
  echo "SKIP js syntax (node not installed)"
fi

echo "== API smoke test (mock mode) =="
"$PY" "$ROOT/tests/smoke_test.py"
