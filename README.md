<p align="center">
  <h1 align="center">stitch</h1>
  <p align="center"><strong>The AI that stitches your CI back together.</strong></p>
  <p align="center">
    Open-source AI agent that autonomously detects, diagnoses, and fixes CI pipeline failures.<br/>
    Platform-agnostic. Orchestrator-agnostic. Zero human intervention required.
  </p>
</p>

<p align="center">
  <a href="https://pypi.org/project/stitch-agent/"><img src="https://img.shields.io/pypi/v/stitch-agent?style=flat-square" alt="PyPI"></a>
  <a href="https://pypi.org/project/stitch-agent/"><img src="https://img.shields.io/pypi/pyversions/stitch-agent?style=flat-square" alt="Python"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue?style=flat-square" alt="License"></a>
</p>

---

**Your CI pipeline just failed.** Again. A missing import. A linting rule. A type error in the code you didn't even touch. You context-switch, open the logs, squint at the error, make a one-line fix, push, wait. Rinse, repeat.

**stitch fixes that for you.** Point it at your failed pipeline, and it will read the logs, classify the error, generate a minimal patch, and open a PR — all in seconds.

## How it works

```
Failed pipeline ──> stitch ──> fix branch ──> CI verifies ──> MR auto-created
```

1. **Fetch** — downloads job logs and the diff that triggered the failure
2. **Classify** — identifies the error type using 150+ patterns and confidence scoring
3. **Fix** — Claude generates a minimal, conservative patch for affected files
4. **Validate** — optionally runs the fix in a Docker sandbox (strict mode)
5. **Branch** — pushes the fix to a `stitch/fix-*` branch
6. **Verify** — CI runs on the fix branch to confirm the fix actually works
7. **PR** — if CI passes, stitch opens a Merge/Pull Request automatically

### Two-phase CI flow

stitch uses a two-phase approach to ensure fixes are verified before creating PRs:

| Phase | Trigger | What happens |
|-------|---------|-------------|
| **Fix** | CI fails on your branch | stitch generates a fix, pushes to `stitch/fix-*` branch (no MR yet) |
| **Verify** | CI passes on `stitch/fix-*` | stitch creates the MR targeting your original branch |

If CI fails on the fix branch, no MR is created — the fix didn't work, and stitch won't create noise.

## Supported error types

| Type | Model used |
|------|-----------|
| `lint` | Haiku |
| `format` | Haiku |
| `simple_type` | Haiku |
| `config_ci` | Haiku |
| `build` | Haiku |
| `complex_type` | Sonnet |
| `test_contract` | Sonnet |
| `logic_error` | Sonnet |
| `unknown` | Sonnet |

All error types are attempted. Classification determines which model to use — simple errors use the faster, cheaper Haiku model; complex errors use Sonnet.

## Quick start

### Install

```bash
pip install stitch-agent                # core (GitLab support included)
pip install "stitch-agent[github]"      # add GitHub support
pip install "stitch-agent[webhook]"     # add webhook server
pip install "stitch-agent[all]"         # everything
```

Requires **Python 3.12+**.

### Set credentials

```bash
export STITCH_ANTHROPIC_API_KEY=sk-ant-...
export STITCH_GITLAB_TOKEN=glpat-...      # or STITCH_GITHUB_TOKEN=ghp_...
```

### Add to your CI (30 seconds)

The fastest way to get stitch running — copy one YAML snippet into your CI config. No server to deploy, no webhooks to configure.

**GitLab CI** — add to `.gitlab-ci.yml`:

```yaml
# Option A: after_script (granular — each job reports its own failure)
# Two jobs: stitch-fix (on failure) + stitch-verify (on success)

.stitch-fix: &stitch-fix
  after_script:
    - pip install stitch-agent
    - stitch ci
  except:
    refs:
      - /^stitch\//

my-lint-job:
  <<: *stitch-fix
  script:
    - ruff check .

# Verify: runs on stitch/fix-* branches when CI passes → creates MR
stitch-verify:
  stage: .post
  when: on_success
  only:
    refs:
      - /^stitch\//
  script:
    - pip install stitch-agent
    - stitch ci
```

```yaml
# Option B: .post stage (catch-all — one job covers the whole pipeline)
# Two jobs: stitch-fix (on failure) + stitch-verify (on success)

stitch-fix:
  stage: .post
  when: on_failure
  except:
    refs:
      - /^stitch\//
  script:
    - pip install stitch-agent
    - stitch ci

stitch-verify:
  stage: .post
  when: on_success
  only:
    refs:
      - /^stitch\//
  script:
    - pip install stitch-agent
    - stitch ci
```

**GitHub Actions** — create `.github/workflows/stitch.yml`:

```yaml
name: stitch-autofix
on:
  workflow_run:
    workflows: ["*"]
    types: [completed]

permissions:
  contents: write
  pull-requests: write

jobs:
  # Phase 1: Fix — runs when CI fails on a non-stitch branch
  fix:
    if: >-
      github.event.workflow_run.conclusion == 'failure' &&
      !startsWith(github.event.workflow_run.head_branch, 'stitch/')
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install "stitch-agent[github]"
      - run: stitch ci
        env:
          STITCH_ANTHROPIC_API_KEY: ${{ secrets.STITCH_ANTHROPIC_API_KEY }}
          STITCH_GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}

  # Phase 2: Verify — runs when CI passes on a stitch/fix-* branch
  verify:
    if: >-
      github.event.workflow_run.conclusion == 'success' &&
      startsWith(github.event.workflow_run.head_branch, 'stitch/')
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install "stitch-agent[github]"
      - run: stitch ci
        env:
          STITCH_ANTHROPIC_API_KEY: ${{ secrets.STITCH_ANTHROPIC_API_KEY }}
          STITCH_GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

That's it. When a pipeline fails, stitch generates a fix and pushes it to a `stitch/fix-*` branch. CI runs on that branch, and if it passes, stitch creates the MR automatically.

> **Two-phase flow:** The fix is verified by your CI before any MR is created. If the fix doesn't pass CI, no MR is opened — zero noise.

> **Loop prevention:** The `except`/`only` rules (GitLab) and branch name conditions (GitHub) ensure stitch only fixes normal branches and only verifies fix branches. The `max_attempts` setting (default 3) caps retries via the API.

> **GitHub note:** `workflow_run` events fire 1-5 minutes after the triggering workflow completes. This is a GitHub platform limitation, not a stitch delay.

#### GitLab: `after_script` vs `.post` stage

| | `after_script` (Option A) | `.post` stage (Option B) |
|---|---|---|
| **Granularity** | Per-job — each job reports its own failure | Per-pipeline — one catch-all job |
| **`CI_JOB_ID`** | Points to the failed job itself | Points to the stitch job (needs API discovery) |
| **Setup** | YAML anchor on each job + shared verify job | Two extra jobs |
| **Best for** | Repos where you want per-job fix PRs | Repos where you want a single fix PR per pipeline |

Both options include the `stitch-verify` job that creates the MR after CI passes on the fix branch.

## CI-native mode

`stitch ci` auto-detects the platform from environment variables and processes all failed jobs in the current pipeline.

```bash
stitch ci                          # auto-detect platform, text output
stitch ci --output json            # machine-readable output
stitch ci --platform gitlab        # override auto-detection
stitch ci --max-jobs 3             # limit jobs processed (default 5)
```

**How detection works:**

| Environment variable | Platform |
|---|---|
| `CI_PROJECT_ID` | GitLab |
| `GITHUB_REPOSITORY` | GitHub |

GitLab mode is further refined by `CI_JOB_STATUS`:
- Present and `"failed"` → `after_script` mode (single job, no API discovery)
- Absent → `.post` stage mode (discovers failed jobs via API)

## Alternative: CLI

For manual use or scripting outside CI:

```bash
stitch fix \
  --platform gitlab \
  --project-id 42 \
  --pipeline-id 9999 \
  --job-id 1234 \
  --branch main
```

### Python API

```python
import asyncio
from stitch_agent import StitchAgent, FixRequest
from stitch_agent.adapters.gitlab import GitLabAdapter

adapter = GitLabAdapter(token="glpat-...", base_url="https://gitlab.com")
agent = StitchAgent(adapter=adapter, anthropic_api_key="sk-ant-...")

request = FixRequest(
    platform="gitlab",
    project_id="42",
    pipeline_id="9999",
    job_id="1234",
    branch="main",
)

async def main():
    async with adapter:
        result = await agent.fix(request)
    print(result.status)   # 'fixed' | 'escalate' | 'error'
    print(result.mr_url)   # MR/PR URL if fixed

asyncio.run(main())
```

### GitHub

```python
from stitch_agent import StitchAgent, FixRequest
from stitch_agent.adapters.github import GitHubAdapter

adapter = GitHubAdapter(token="ghp_...")
agent = StitchAgent(adapter=adapter, anthropic_api_key="sk-ant-...")

request = FixRequest(
    platform="github",
    project_id="org/repo",
    pipeline_id="12345678",   # workflow run ID
    job_id="abc123def",       # head SHA
    branch="feature/my-fix",
)
```

Works with GitHub Actions workflow runs. Self-hosted GitHub Enterprise is supported via `STITCH_GITHUB_BASE_URL`.

## Alternative: Webhook server

For environments where you prefer a centralized server over per-repo CI jobs.

## Onboarding

stitch includes three commands to get you set up fast:

### `stitch setup` — auto-detect your project

```bash
stitch setup --repo . --platform gitlab
```

Scans your repo and generates a `.stitch.yml` config by detecting:
- **Languages** — Python, TypeScript, Go, Ruby
- **Linter** — ruff, eslint, golangci-lint
- **Test runner** — pytest, jest, vitest, rspec, go test
- **Package manager** — pip, npm, yarn, pnpm, bun, go
- **CI provider** — GitLab CI, GitHub Actions

### `stitch doctor` — health check

```bash
stitch doctor --repo . --platform gitlab --project-id 42
```

Runs 15+ diagnostic checks:
- Python version compatibility
- API keys and tokens configured
- Platform API connectivity
- OAuth scopes and permissions
- Webhook access
- Docker availability (for strict mode)

Returns clear remediation steps for any issues found.

### `stitch connect` — auto-provision webhooks

```bash
stitch connect --repo . --platform gitlab --project-id 42 \
  --webhook-url https://your-server:8000/webhook/gitlab
```

Automatically creates the webhook in your GitLab/GitHub project so pipeline failures trigger stitch. No manual setup required.

Deploy the webhook server and let your CI platform notify it on failures.

```bash
pip install "stitch-agent[webhook]"

export STITCH_ANTHROPIC_API_KEY=sk-ant-...
export STITCH_GITLAB_TOKEN=glpat-...
export STITCH_WEBHOOK_SECRET=my-hmac-secret

python -m runners.webhook
# or: stitch-webhook
```

### Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/webhook/gitlab` | POST | Receives GitLab pipeline failure events |
| `/webhook/github` | POST | Receives GitHub workflow_run failure events |
| `/health` | GET | Health check (`{"status": "ok"}`) |

### Security

- **HMAC signature verification** — GitLab token or GitHub SHA256 HMAC
- **API key authentication** — Bearer token support
- **Rate limiting** — per-IP sliding window (configurable)

### GitLab setup

**Settings > Webhooks > Add webhook**
- URL: `https://your-server:8000/webhook/gitlab`
- Secret token: your `STITCH_WEBHOOK_SECRET`
- Trigger: **Pipeline events**

### GitHub setup

**Settings > Webhooks > Add webhook**
- Payload URL: `https://your-server:8000/webhook/github`
- Content type: `application/json`
- Secret: your `STITCH_WEBHOOK_SECRET`
- Events: **Workflow runs**

## Configuration

### Environment variables

```bash
# Required
STITCH_ANTHROPIC_API_KEY=sk-ant-...

# Platform credentials (at least one)
STITCH_GITLAB_TOKEN=glpat-...
STITCH_GITHUB_TOKEN=ghp_...

# Base URLs (for self-hosted instances)
STITCH_GITLAB_BASE_URL=https://gitlab.example.com
STITCH_GITHUB_BASE_URL=https://github.example.com/api/v3

# Confidence thresholds (0.0–1.0)
STITCH_HAIKU_CONFIDENCE_THRESHOLD=0.80    # default
STITCH_SONNET_CONFIDENCE_THRESHOLD=0.40   # default

# Validation
STITCH_VALIDATION_MODE=trusted            # or 'strict' (requires Docker)
STITCH_MAX_ATTEMPTS=3

# Webhook server
STITCH_WEBHOOK_HOST=0.0.0.0
STITCH_WEBHOOK_PORT=8000
STITCH_WEBHOOK_SECRET=...
STITCH_WEBHOOK_API_KEYS=key1,key2
STITCH_WEBHOOK_RATE_LIMIT=60              # requests per window
STITCH_WEBHOOK_RATE_WINDOW=60             # seconds
```

### Per-repo config (`.stitch.yml`)

Place a `.stitch.yml` in your repo root to customize behavior per project:

```yaml
languages: [python, typescript]
linter: ruff
test_runner: pytest
package_manager: pip

# Custom conventions the AI should follow
conventions:
  - "Always use explicit return types on public functions."
  - "Never downgrade dependency versions."

# Which error types to auto-fix
auto_fix:
  - lint
  - format
  - simple_type
  - config_ci
  - complex_type
  - test_contract

# Which to escalate (never auto-fix)
escalate:
  - logic_errors
  - breaking_changes

max_attempts: 3

# Override Docker image for strict validation
docker_image: python:3.12-slim

# Notifications on escalation
notify:
  timeout_seconds: 10.0
  fanout: parallel
  channels:
    - type: slack
      webhook_url: https://hooks.slack.com/services/xxx/yyy/zzz
    - type: webhook
      url: https://hooks.example.com/stitch
```

Run `stitch setup` to auto-generate this file, or copy `.stitch.example.yml` from this repo.

## Validation modes

### Trusted (default)

Generates the fix and pushes to a fix branch. In CI mode, the MR is created after CI verifies the fix. In CLI mode (`stitch fix`), the MR is created immediately. Fast, no extra dependencies.

### Strict (requires Docker)

Applies the patch in an isolated Docker container and runs your test suite before opening a PR. Only creates the PR if tests pass.

```bash
STITCH_VALIDATION_MODE=strict stitch fix ...
```

Docker images are auto-selected based on your language:

| Language | Image |
|----------|-------|
| Python | `python:3.12-slim` |
| JavaScript/TypeScript | `node:20-slim` |
| Go | `golang:1.22-alpine` |
| Ruby | `ruby:3.3-slim` |

Override with `docker_image` in `.stitch.yml`.

## Notifications and escalation

When stitch can't auto-fix an error (low confidence or unsupported error type), it escalates. Configure notification channels so your team knows immediately:

```yaml
# .stitch.yml
notify:
  channels:
    - type: slack
      webhook_url: https://hooks.slack.com/services/xxx/yyy/zzz
    - type: webhook
      url: https://hooks.example.com/stitch
```

Or use the programmatic escalation callback:

```python
async def on_escalate(request, result):
    await slack.post(f"Human needed: {result.reason} on {request.branch}")

agent = StitchAgent(
    adapter=adapter,
    anthropic_api_key="sk-ant-...",
    escalation_callback=on_escalate,
)
```

## Fix history

stitch records every fix attempt locally in SQLite for auditing and pattern analysis:

```python
from stitch_agent.history import HistoryStore

with HistoryStore(".stitch/history.db") as store:
    # Recent fix attempts
    records = store.get_recent("org/repo", limit=20)

    # Success rate by error type
    pattern = store.get_pattern("org/repo", "lint")
    print(f"Lint success rate: {pattern.success_rate:.0%}")
    print(f"Total: {pattern.total} | Fixed: {pattern.fixed} | Escalated: {pattern.escalated}")
    print(f"Avg confidence: {pattern.avg_confidence:.2f}")
```

History is stored automatically at `{workspace_root}/.stitch/history.db`.

## Orchestrator integrations

Ready-to-use examples for popular workflow engines:

| Engine | File | Use case |
|--------|------|----------|
| **Prefect** | `runners/examples/prefect_runner.py` | Poll projects for failures, auto-fix with retries |
| **Temporal** | `runners/examples/temporal_runner.py` | Durable workflow with 10-min timeout per fix |
| **Dagster** | `runners/examples/dagster_runner.py` | Sensor-based trigger with web UI dashboard |

Each example is copy-paste ready — set your env vars and run.

## CLI reference

```bash
# CI-native mode (recommended — run inside your CI pipeline)
stitch ci [--output json|text] [--platform gitlab|github] [--max-jobs 5]

# Fix a specific failed job
stitch fix \
  --platform gitlab|github \
  --project-id <id> \
  --pipeline-id <pipeline_id> \
  --job-id <job_id> \
  --branch <branch> \
  [--job-name <name>] \
  [--gitlab-url <url>] \
  [--github-url <url>] \
  [--haiku-threshold 0.80] \
  [--sonnet-threshold 0.40] \
  [--output json|text]

# Auto-detect project and generate .stitch.yml
stitch setup --repo . --platform gitlab|github [--json]

# Run health checks
stitch doctor --repo . --platform gitlab|github [--project-id <id>] [--json]

# Auto-provision webhook in your CI platform
stitch connect --repo . --platform gitlab|github [--project-id <id>] \
  [--webhook-url <url>] [--json]
```

**Exit codes:** `0` = success/fixed, `1` = error/escalated, `2` = prompts needed

All commands support `--json` for machine-readable output.

## Architecture

```
stitch_agent/
├── adapters/           # Platform integrations (GitLab, GitHub)
├── core/
│   ├── agent.py        # Main fix loop
│   ├── classifier.py   # Error detection (150+ patterns)
│   ├── fixer.py        # Claude-powered patch generation
│   ├── validator.py    # Docker sandbox validation
│   └── notifier.py     # Multi-channel notifications
├── onboarding/         # setup, doctor, connect commands
├── models.py           # FixRequest, FixResult, ErrorType
├── history.py          # SQLite fix tracking
├── settings.py         # Environment configuration
└── config.py           # .stitch.yml parsing

runners/
├── cli.py              # CLI entry point
├── ci_runner.py        # CI-native runner (auto-detect platform)
├── webhook.py          # FastAPI webhook server
└── examples/           # Prefect, Temporal, Dagster integrations
```

## Contributing

```bash
git clone https://github.com/g24r/stitch.git
cd stitch
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check .

# Type check
pyright
```

## Publishing

```bash
pip install "stitch-agent[publish]"
python -m build
twine upload dist/*
```

Or push a `v*` tag to trigger the GitHub Actions publish workflow (uses PyPI Trusted Publisher).

## License

MIT
