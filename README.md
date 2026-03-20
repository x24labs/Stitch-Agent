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
Failed pipeline ──> stitch ──> Merge/Pull Request with the fix
```

1. **Fetch** — downloads job logs and the diff that triggered the failure
2. **Classify** — identifies the error type using 150+ patterns and confidence scoring
3. **Fix** — Claude generates a minimal, conservative patch for affected files
4. **Validate** — optionally runs the fix in a Docker sandbox before opening a PR (strict mode)
5. **PR** — opens a Merge/Pull Request with a Conventional Commits message

## Supported error types

| Type | Model used | Action |
|------|-----------|--------|
| `lint` | Haiku | auto-fix |
| `format` | Haiku | auto-fix |
| `simple_type` | Haiku | auto-fix |
| `config_ci` | Haiku | auto-fix |
| `build` | Haiku | auto-fix |
| `complex_type` | Sonnet | auto-fix |
| `test_contract` | Sonnet | auto-fix |
| `logic_error` | — | escalate to human |
| `unknown` | — | escalate to human |

Simple errors use the faster, cheaper Haiku model. Complex errors use Sonnet. Errors stitch can't safely fix are escalated with context so a human can resolve them quickly.

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

### Fix a failed pipeline (CLI)

```bash
stitch fix \
  --platform gitlab \
  --project-id 42 \
  --pipeline-id 9999 \
  --job-id 1234 \
  --branch main
```

That's it. stitch reads the logs, generates a fix, and opens an MR.

### Fix a failed pipeline (Python)

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

## GitHub support

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

## Webhook server

The easiest way to run stitch in production — deploy the webhook server and let your CI platform notify it on failures.

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

Generates the fix and opens a PR immediately. Fast, no extra dependencies.

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
# Fix a failed pipeline
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
