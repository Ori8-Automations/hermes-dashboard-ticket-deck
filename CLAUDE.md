# Working in this repo

Ticket Deck, a read-only Hermes Dashboard plugin for Zammad tickets. `README.md` explains how to
install and configure it and documents its API and safety posture.

- The plugin is read-only by design: only `GET` routes, no ticket writes, no attachment downloads.
  Keep it that way unless Mike says otherwise.
- Run the checks with `./tests/run_tests.sh`. The smoke test runs in mock mode with no Zammad. It
  needs a Python with `fastapi` and `httpx`; point `PYTHON` at a venv that has them.
- This repo is public. Never commit a Zammad token, a `zammad.env` file, a real Zammad URL or
  hostname, or real ticket content; demo data stays in the canned mock fixtures.
- Start from the default branch, `main`. When the work is done, open a pull request into `main` and
  tell Mike it is ready to merge. Finished work should never be left on a branch without a pull request.
