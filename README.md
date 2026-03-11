# stitch

The AI that stitches your CI back together.

Open-source AI agent that autonomously detects, diagnoses, and fixes CI pipeline failures. Platform-agnostic. Orchestrator-agnostic.

## Install

```bash
pip install stitch-agent               # GitLab + basic
pip install "stitch-agent[github]"    # add GitHub support
pip install "stitch-agent[webhook]"   # add webhook server
pip install "stitch-agent[all]"       # everything
```

## Quick start

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

## How it works

1. **Fetch** — downloads job logs and the diff that triggered the pipeline
2. **Classify** — Claude Haiku/Sonnet identifies the error type and confidence
3. **Fix** — generates a patch for affected files
4. **Validate** — optionally runs the fixer in a Docker sandbox (strict mode)
5. **PR** — opens a Merge/Pull Request with the fix

### Error types

| Type | Model | Auto-fixed |
|------|-------|------------|
| `lint` | Haiku | yes |
| `format` | Haiku | yes |
| `simple_type` | Haiku | yes |
| `config_ci` | Haiku | yes |
| `complex_type` | Sonnet | yes |
| `test_contract` | Sonnet | yes |
| `logic_error` | — | escalate |
| `unknown` | — | escalate |

## Configuration

All settings are read from environment variables with the `STITCH_` prefix.

```bash
# Required
STITCH_ANTHROPIC_API_KEY=sk-ant-...
STITCH_GITLAB_TOKEN=glpat-...       # or STITCH_GITHUB_TOKEN

# Thresholds (0.0–1.0)
STITCH_HAIKU_CONFIDENCE_THRESHOLD=0.80
STITCH_SONNET_CONFIDENCE_THRESHOLD=0.40

# Validation
STITCH_VALIDATION_MODE=trusted       # or 'strict' (requires Docker)
STITCH_MAX_ATTEMPTS=3

# Webhook
STITCH_WEBHOOK_SECRET=...           # HMAC secret for GitLab/GitHub
STITCH_WEBHOOK_API_KEYS=key1,key2   # Bearer token auth
STITCH_WEBHOOK_RATE_LIMIT=60        # requests per window
STITCH_WEBHOOK_RATE_WINDOW=60       # seconds
STITCH_WEBHOOK_HOST=0.0.0.0
STITCH_WEBHOOK_PORT=8000
```

Or via `.stitch.yml` in the repo root:

```yaml
languages: [python]
linter: ruff
test_runner: pytest
auto_fix:
  - lint
  - format
  - simple_type
  - config_ci
  - complex_type
  - test_contract
escalate:
  - logic_errors
  - breaking_changes
notify:
  slack_webhook: https://hooks.slack.com/...
max_attempts: 3
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

## Webhook server

The easiest way to integrate stitch into your CI platform:

```bash
pip install "stitch-agent[webhook]"
export STITCH_ANTHROPIC_API_KEY=sk-ant-...
export STITCH_GITLAB_TOKEN=glpat-...
export STITCH_WEBHOOK_SECRET=my-secret
export STITCH_WEBHOOK_API_KEYS=token1,token2

python -m runners.webhook
# or: stitch-webhook  (after pip install)
```

Then point your GitLab pipeline webhook at `http://your-server:8000/webhook/gitlab`.

GitLab setup: **Settings → Webhooks → Pipeline events** with your `STITCH_WEBHOOK_SECRET` as the token.

GitHub setup: **Settings → Webhooks → workflow_run** with HMAC using `STITCH_WEBHOOK_SECRET`.

## Fix history

stitch records every fix attempt locally for pattern learning and auditing:

```python
from stitch_agent.history import HistoryStore

with HistoryStore(".stitch/history.db") as store:
    records = store.get_recent("org/repo", limit=20)
    pattern = store.get_pattern("org/repo", "lint")
    print(f"lint success rate: {pattern.success_rate:.0%}")
```

The agent stores history automatically in `{workspace_root}/.stitch/history.db`.

## Escalation hooks

Run custom logic when stitch cannot auto-fix:

```python
async def on_escalate(request, result):
    await slack.post(f"Human needed: {result.reason} on {request.branch}")

agent = StitchAgent(
    ...,
    escalation_callback=on_escalate,
)
```

## Orchestrator integrations

Copy-paste examples for popular workflow engines:

| Engine | File |
|--------|------|
| Prefect | `runners/examples/prefect_runner.py` |
| Temporal | `runners/examples/temporal_runner.py` |
| Dagster | `runners/examples/dagster_runner.py` |

## CLI

```bash
stitch fix \
  --platform gitlab \
  --project-id 42 \
  --pipeline-id 9999 \
  --job-id 1234 \
  --branch main
```

## Strict validation mode

Requires Docker. Applies the patch in a fresh clone and runs the repo's test suite before opening a PR.

```bash
STITCH_VALIDATION_MODE=strict stitch fix ...
```

## Publish to PyPI

```bash
pip install "stitch-agent[publish]"
python -m build
twine upload dist/*
```

Or push a `v*` tag to trigger the GitHub Actions workflow.

## License

MIT
