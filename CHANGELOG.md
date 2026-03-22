# Changelog

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
