# Changelog

## v2.0.0, 2026-04-16

Full rewrite from Python to TypeScript. Distributed via npm, zero runtime dependency on Python.

### Changed (breaking)

- Package renamed from PyPI `stitch-agent` (Python) to npm `stitch-agent` (TypeScript). Install via `npm i -g stitch-agent` or `bunx stitch-agent`.
- Entry binary now runs on Node 20+ / Bun 1.0+. Python 3.12 requirement removed.
- CLI surface preserved: `stitch run`, `stitch generate`, `stitch doctor`, `stitch history`.

### Added

- `stitch doctor` diagnostic command (runtime, CI config, agent CLI, permissions).
- `stitch history` with streak-compacted fix history.
- `.stitch.yml` configuration file (agent, maxAttempts, failFast, jobs, push, watch, debounce).
- `--fail-fast` flag cancels in-flight jobs on first failure before the fix loop.
- Bitbucket Pipelines support alongside GitLab CI and GitHub Actions.
- OpenTUI renderer replaces the raw ANSI TUI for flicker-free output.
- Auto-commit and auto-push after a successful fix loop (disabled via `--no-push`).
- Global error handler in the CLI: unhandled errors print `stitch: <msg>` instead of raw Node stack traces. `STITCH_DEBUG=1` re-enables stacks.
- Automated npm publish from `release/*` branches via GitLab CI (issue #34).

### Removed

- Python codebase (`stitch_agent/`, `runners/`, `pyproject.toml`). Full history remains in git.
- `@orchetron/storm` + React TUI runtime (8MB+1.5MB+260KB), replaced by OpenTUI.

## v1.1.0, 2026-04-12

### Added

- LLM-based job classification replaces hardcoded pattern matching. Infra jobs (deploy, publish, docker push) are skipped by default; verify jobs (lint, test, build) run. Cached in `.stitch/jobs.json` keyed by job-name hash.
- Auto-detect CI platform (GitLab, GitHub) so `--jobs` is no longer required.
- `stitch generate <agent>` command: LLM analyzes the repo and proposes CI test/lint jobs.

### Fixed

- Product name capitalization ("Stitch") standardized across all user-facing messages.

## v1.0.0, 2026-04-10

First stable release. Pivot to skill-first local CI runner.

### Changed (breaking)

- Project repositioned from CI-hosted webhook service to local-first CLI. Runs your CI jobs on the developer machine and delegates fixes to a local agent CLI (Claude Code or Codex).
- Zero-config mode: `.stitch.yml` no longer required for normal use.
- Anthropic API driver removed. Agent CLI (claude, codex) is now the only fix backend; no API keys handled by Stitch.

### Added

- Rich TUI for `stitch run` (progress bar, per-job status, footer).
- `--permission-mode acceptEdits` passed to the Claude Code CLI so fixes apply without prompts.

### Removed

- OpenAI API driver (`stitch_agent/drivers/api.py`), `openai` dependency.
- Webhook server, orchestrator examples, Docker sandbox validation mode (already removed in v0.1.5, cleanup finalized).
- `pydantic` and `httpx` dependencies (zero-config refactor).

---

## Pre-v1.0.0 (Python era)

Entries below describe the pre-v1 Python/webhook implementation. Kept for historical reference. Not applicable to the current TypeScript codebase.

## v0.2.1 — 2026-03-24

### Removed

- **`stitch connect` command** — vestigial webhook provisioning (docs removed in v0.1.5, code removed now)
- Dead dependencies: `gitpython`, `python-gitlab`, `pygithub`, `fastapi`, `uvicorn`
- `webhook_secret` setting, `docker_image` config field, `_run_not_implemented()` placeholder
- `after_script` CI mode — simplified to `.post` stage only
- Stale `ISSUE-unrecognized-error-patterns.md`

### Fixed

- `__version__` in `__init__.py` now matches `pyproject.toml` (was stuck at 0.1.0)
- README: added missing `patch_validator.py` to architecture tree, added `validation` config to `.stitch.yml` example, fixed intro text, added `LICENSE` file

## v0.2.0 — 2026-03-23

### Added

- **Auto-repair with model escalation** — when a fix fails CI, stitch automatically retries on the same branch instead of escalating to human review. Tracks attempt count via branch commits. After initial retries, escalates to Sonnet model. Only escalates to human after all attempts exhausted.
- `retry_fix` method on `StitchAgent` — generates and pushes fixes to an existing fix branch with model override support.
- `push_to_branch` and `count_branch_commits` on adapters (GitLab + GitHub).
- New CI runner statuses: `retried` (fix pushed), `fix_exhausted` (max attempts reached, human needed), `retry_error`, `retry_failed`.

### Fixed

- **Classifier fails to extract file paths from GitLab logs** — ANSI escape codes in job traces broke the file reference regex. Now strips ANSI codes from job logs.

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
  - GitLab: `.post` stage (catch-all)
  - GitHub: `workflow_run` event trigger
  - Loop prevention via branch exclusion + `max_attempts`
- **Error classification** — 150+ patterns across 9 error types
- **AI-powered fixes** — Haiku for simple errors, Sonnet for complex ones
- **Automatic PR/MR creation** with Conventional Commits messages
- **Multi-channel escalation** — Slack, webhook, and custom notifications
- **Onboarding commands** — `stitch setup`, `stitch doctor`
- **Fix history** — SQLite-backed tracking with pattern analytics
- **Platform support** — GitLab, GitHub (including self-hosted)
