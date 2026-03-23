# Changelog

## v0.1.10 — 2026-03-23

### Fixed

- **Classifier fails to extract file paths from GitLab logs** — ANSI escape codes in job traces (`\x1b[96msrc/file.ts\x1b[0m`) broke the file reference regex, causing `affected_files=[]` and empty `file_contents`. Now strips ANSI codes from job logs in `fetch_job_logs`.

## v0.1.8 — 2026-03-23

### Fixed

- **`fetch_file_content` 400/403 on self-hosted GitLab** — reverse proxies (Cloudflare, nginx) reject `%2F`-encoded slashes in GitLab files API paths. Now falls back to tree listing + blob endpoint when the primary API returns 400 or 403.

## v0.1.7 — 2026-03-23

### Added

- **Patch validation gate** — programmatic validation of LLM-generated patches before pushing to fix branches. Rejects destructive fixes that rewrite entire files, change function signatures, remove exports, or add new dependencies. Escalates instead of pushing broken code.
- `ValidationConfig` in `.stitch.yml` — configurable thresholds: `max_diff_ratio`, `max_files_changed`, `max_lines_changed`, `block_new_imports`, `block_signature_changes`, `block_export_removal`.
- `PatchValidator` module with language-aware checks for Python, TypeScript, and JavaScript (diff ratio, export removal, signature preservation, new import detection, delete protection).
- 15 new tests covering all validation checks including a real-world reproduction of the `config.ts` rewrite bug.

## v0.1.6 — 2026-03-23

### Fixed

- **stitch-check 404 on self-hosted GitLab** — `get_latest_commit_message` API call failed on instances where the token lacked `read_repository` scope. Now reads `CI_COMMIT_MESSAGE` env var first (always available in GitLab CI), falls back to API only when needed.
- API fallback errors are suppressed gracefully — no more unhandled 404 crashes in verify/escalate mode.
- **Non-conservative fixes breaking callers** — hardened fixer prompt with explicit constraints: never change function signatures, type definitions, exports, or unrelated lines. If a fix requires signature changes, the LLM now returns an empty patch (escalates) instead of introducing new errors.

### Added

- `commit_message` field on `CIContext` dataclass — carries commit message from CI environment.
- Three new tests: env var path, API fallback, and 404 resilience.

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
- `stitch-check` replaces separate `stitch-verify`/`stitch-retry` jobs — single job with `when: always`
- On stitch/fix-* branches, auto-detects verify vs escalate by checking for failed jobs in pipeline
- When fix doesn't pass CI, escalates with clear message instead of silent failure

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
