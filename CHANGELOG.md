# Changelog

## v0.1.5 — 2026-03-23

### Removed

- **Webhook server** — `runners/webhook.py` and all webhook configuration (stitch lives in CI now)
- **Orchestrator examples** — `runners/examples/` (Prefect, Temporal, Dagster) — CI-native approach replaces them
- **Strict validation mode** — Docker sandbox (`validator.py`, `workspace.py`) — CI pipeline verifies fixes natively
- `validation_mode` setting and all webhook-related env vars from settings
- Contributing section and `stitch connect` command from README

### Changed

- README rewritten: focused on CI-native approach, removed all webhook/server documentation
- Architecture diagram updated to reflect simplified codebase

## v0.1.4 — 2026-03-23

### Added

- **Two-phase CI flow** — fixes are verified by CI before creating MRs
  - Phase 1 (fix): generate fix → push to `stitch/fix-*` branch (no MR)
  - Phase 2 (verify): CI passes on fix branch → create MR automatically
- `Stitch-Target` commit trailer for tracking target branch across phases
- `get_latest_commit_message()` method on adapters (GitLab + GitHub)
- `stitch-verify` CI job template for both GitLab and GitHub

### Changed

- `stitch ci` auto-detects verify mode when running on a `stitch/fix-*` branch
- `agent.fix()` accepts `create_mr` parameter (default `True`, CI sets `False`)
- Updated CI templates: two jobs (fix + verify) instead of one
- Documentation rewritten to reflect two-phase flow

## v0.1.3 — 2026-03-23

### Changed

- Classification now routes all error types to a fix attempt — no more auto-escalation
- `ESCALATION_TYPES` emptied; `LOGIC_ERROR` and `UNKNOWN` moved to `SONNET_TYPES`

### Fixed

- TypeScript error pattern now matches `ts(2365)` format (case-insensitive, optional parens)

## v0.1.2 — 2026-03-22

### Fixed

- Fix HTTP client closed error in CI runner — unified adapter session for job discovery and processing
- Fix publish pipeline uploading stale artifacts — clean dist/ before rebuild

## v0.1.0 — 2026-03-22

Initial public release.

### Features

- **CI-native mode** (`stitch ci`) — auto-detect GitLab/GitHub from env vars, zero config
  - GitLab: `after_script` (per-job) and `.post` stage (catch-all) modes
  - GitHub: `workflow_run` event trigger
  - Loop prevention via branch exclusion + `max_attempts`
- **Error classification** — 150+ patterns across 9 error types
- **AI-powered fixes** — Haiku for simple errors, Sonnet for complex ones
- **Automatic PR/MR creation** with Conventional Commits messages
- **Strict validation mode** — Docker sandbox verification before opening PRs
- **Multi-channel escalation** — Slack, webhook, and custom notifications
- **Onboarding commands** — `stitch setup`, `stitch doctor`, `stitch connect`
- **Fix history** — SQLite-backed tracking with pattern analytics
- **Platform support** — GitLab, GitHub (including self-hosted)
