# PRD-21198 · Code review

## Round 1 · 2026-10-29 · reviewed dev/PRD-21198 @ 7b8030f (base main @ 14a8291)

Status: frozen @ 7b8030f

Verdict: approved

### Scope

`git diff 14a8291...7b8030f` on `MSPbotsAIFramework/datto-rmm-mcp`: new
`src/datto_rmm_mcp/tools/alerts.py` (`dattormm_get_alerts`,
`dattormm_resolve_alert`), one-line `DattoClient.post()` addition, the
`server.py` registration + `from __future__ import annotations` fix,
README updates, and matching test changes across
`test_alerts.py`/`test_paging.py`/`test_isolation.py`/`test_middleware.py`/
`test_tools.py`.

### Findings

1. [suggestion] `README.md:16-22` — the `## MSPbots Connector` boilerplate
   section is shorter and out of date versus
   `vendor-mcp-development/reference/readme-connector-section.md` (missing
   the "Why Connector" / "Quick start" / "Features" / "Pricing" blocks the
   current template has). Confirmed this predates this ticket (identical at
   base `14a8291`, `git show 14a8291:README.md`) and the diff does not touch
   those lines — the tech design explicitly scoped this section as untouched.
   Not this ticket's defect; worth a standalone doc-sync ticket for this
   connector (and likely others pre-dating the current template revision).

No must-fix findings.

### Verification performed

- Re-ran gates independently on `7b8030f` (not just re-read the Self-test
  section): `uv sync`, `uv run pytest -q` → 52 passed (matches the Self-test
  table exactly), `uv run ruff check .` → all checks passed, `uv run ruff
  format --check .` → 21 files already formatted, `grep -rnP
  '[\x{4e00}-\x{9fff}]' --exclude-dir=.git --exclude-dir=.venv .` → no output.
- Diffed every changed file against the tech design's Change list
  (`docs/tickets/PRD-21198.md` § Tech design) — one-to-one match, no
  undocumented change found.
- Confirmed `origin/main` is still at `14a8291` (`git fetch origin main
  --force`) — unchanged since the dev leg synced, so no pre-merge rebase is
  needed for this repo.
- Confirmed AC1-AC12 in `docs/tickets/PRD-21198.ac.json` all carry
  `result: pass` with reproducible evidence (test ids); TC-DATTO-013/014
  are `untested` with a documented reason (no real Datto tenant available),
  which is not a blocker per `references/mcp-track.md` §2.5.
- Checked the SOP §14 delivery checklist items that this diff touches:
  structured error envelope (`error_envelope`/`DattoError.to_envelope`,
  unchanged), request-level isolation (unchanged `contextvars` mechanism,
  extended test coverage via AC11/TC-DATTO-011), credentials only from
  Header (unchanged `client_factory()` path, no new credential surface),
  no new dependency.

### Fixes

N/A — no must-fix in this round.
