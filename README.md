<p align="center">
  <h1 align="center">stitch</h1>
  <p align="center"><strong>The AI that stitches your CI back together.</strong></p>
  <p align="center">
    Open-source AI agent that autonomously detects, diagnoses, and fixes CI pipeline failures.<br/>
    Drop two jobs into your CI config. That's it. No servers, no setup, no babysitting.
  </p>
</p>

<p align="center">
  <a href="https://pypi.org/project/stitch-agent/"><img src="https://img.shields.io/pypi/v/stitch-agent?style=flat-square" alt="PyPI"></a>
  <a href="https://pypi.org/project/stitch-agent/"><img src="https://img.shields.io/pypi/pyversions/stitch-agent?style=flat-square" alt="Python"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue?style=flat-square" alt="License"></a>
</p>

---

**Your CI pipeline just failed.** Again. A missing import. A linting rule. A type error in code you didn't even touch. You context-switch, open the logs, squint at the error, make a one-line fix, push, wait. Rinse, repeat.

**stitch fixes that in seconds.** It reads the logs, classifies the error, generates a minimal patch, verifies it passes CI, and opens a PR — fully autonomously, with zero human intervention.

```
Failed pipeline ──> stitch ──> fix branch ──> CI verifies ──> PR auto-created
```

## Why stitch?

- **Zero infrastructure** — runs as a CI job, not a separate service. Nothing to deploy or maintain.
- **Self-healing** — if the first fix doesn't pass CI, stitch retries with model escalation. Only escalates to humans after exhausting all attempts.
- **Safe by design** — every patch goes through programmatic validation before pushing: diff ratio limits, signature preservation, export protection, import guards. The LLM can't rewrite your codebase.
- **Cost-aware** — simple errors (lint, format, types) use the fast, cheap Haiku model. Complex errors escalate to Sonnet. You only pay for what you need.
- **Platform-agnostic** — GitLab and GitHub, including self-hosted instances.

## Quick start

### 1. Install

```bash
pip install stitch-agent
```

Requires Python 3.12+.

### 2. Set credentials

Add these as CI/CD variables (Settings > CI/CD > Variables):

```bash
STITCH_ANTHROPIC_API_KEY=sk-ant-...
STITCH_GITLAB_TOKEN=glpat-...      # or STITCH_GITHUB_TOKEN for GitHub
```

### 3. Add to your CI

<details open>
<summary><strong>GitLab CI</strong> — add to <code>.gitlab-ci.yml</code></summary>

```yaml
stitch-fix:
  stage: .post
  when: on_failure
  image: python:3.12-slim
  except:
    refs:
      - /^stitch\//
  script:
    - pip install stitch-agent
    - stitch ci

stitch-check:
  stage: .post
  when: always
  image: python:3.12-slim
  only:
    refs:
      - /^stitch\//
  script:
    - pip install stitch-agent
    - stitch ci
```

</details>

<details>
<summary><strong>GitHub Actions</strong> — create <code>.github/workflows/stitch.yml</code></summary>

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
  fix:
    if: >-
      github.event.workflow_run.conclusion == 'failure' &&
      !startsWith(github.event.workflow_run.head_branch, 'stitch/')
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install stitch-agent
      - run: stitch ci
        env:
          STITCH_ANTHROPIC_API_KEY: ${{ secrets.STITCH_ANTHROPIC_API_KEY }}
          STITCH_GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}

  check:
    if: startsWith(github.event.workflow_run.head_branch, 'stitch/')
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install stitch-agent
      - run: stitch ci
        env:
          STITCH_ANTHROPIC_API_KEY: ${{ secrets.STITCH_ANTHROPIC_API_KEY }}
          STITCH_GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

</details>

**Done.** When a pipeline fails, stitch pushes a fix to a `stitch/fix-*` branch. CI runs on that branch:

- **CI passes** → stitch creates the PR automatically
- **CI fails** → stitch retries on the same branch (escalating to Sonnet after 2 attempts)
- **All retries exhausted** → stitch notifies your team for human review

> **Loop prevention:** The `except`/`only` rules (GitLab) and branch conditions (GitHub) ensure stitch only fixes normal branches. `max_attempts` (default 3) caps retries.

## How it works

| Phase | Trigger | What happens |
|-------|---------|-------------|
| **Fix** | CI fails on your branch | Generates fix, pushes to `stitch/fix-*` branch |
| **Retry** | CI fails on `stitch/fix-*` | Retries on the same branch, escalating model after 2 attempts |
| **Verify** | CI passes on `stitch/fix-*` | Creates PR targeting your original branch |
| **Exhaust** | Max retries reached | Notifies team via Slack/webhook |

### Error classification

stitch classifies errors to choose the right model and strategy:

| Type | Model | Examples |
|------|-------|---------|
| `lint` | Haiku | Unused imports, missing semicolons, style violations |
| `format` | Haiku | Indentation, trailing whitespace, line length |
| `simple_type` | Haiku | Missing type annotations, basic type mismatches |
| `config_ci` | Haiku | YAML syntax, missing CI variables, stage ordering |
| `build` | Haiku | Missing dependencies, import errors, module resolution |
| `complex_type` | Sonnet | Generic type inference, conditional types, overloads |
| `test_contract` | Sonnet | Broken test assertions, mock mismatches, fixture errors |
| `logic_error` | Sonnet | Off-by-one errors, incorrect conditions, edge cases |
| `unknown` | Sonnet | Unclassified errors — Sonnet handles the ambiguity |

### Patch safety

Every patch is validated **before** pushing. stitch rejects fixes that:

- Rewrite more than 40% of a file (diff ratio guard)
- Change function signatures or remove exports
- Add new dependencies or imports
- Delete files
- Modify more than 5 files or 200 lines

If validation fails, stitch escalates instead of pushing broken code. All thresholds are [configurable](#per-repo-config).

## Configuration

### Environment variables

```bash
# Required
STITCH_ANTHROPIC_API_KEY=sk-ant-...

# Platform credentials (at least one)
STITCH_GITLAB_TOKEN=glpat-...
STITCH_GITHUB_TOKEN=ghp_...

# Self-hosted instances
STITCH_GITLAB_BASE_URL=https://gitlab.example.com
STITCH_GITHUB_BASE_URL=https://github.example.com/api/v3

# Tuning (optional)
STITCH_HAIKU_CONFIDENCE_THRESHOLD=0.80    # default
STITCH_SONNET_CONFIDENCE_THRESHOLD=0.40   # default
STITCH_MAX_ATTEMPTS=3                     # default
```

### Per-repo config

Place a `.stitch.yml` in your repo root to customize behavior. Run `stitch setup` to auto-generate it.

```yaml
languages: [python, typescript]
linter: ruff
test_runner: pytest
package_manager: pip

conventions:
  - "Always use explicit return types on public functions."
  - "Never downgrade dependency versions."

max_attempts: 3

validation:
  max_diff_ratio: 0.40
  max_files_changed: 5
  max_lines_changed: 200
  block_new_imports: true
  block_signature_changes: true
  block_export_removal: true

notify:
  channels:
    - type: slack
      webhook_url: https://hooks.slack.com/services/xxx/yyy/zzz
    - type: webhook
      url: https://hooks.example.com/stitch
```

### Notifications

When stitch exhausts all fix attempts, it notifies your team. Configure channels in `.stitch.yml` or use the programmatic callback:

```python
async def on_escalate(request, result):
    await slack.post(f"Human needed: {result.reason} on {request.branch}")

agent = StitchAgent(
    adapter=adapter,
    anthropic_api_key="sk-ant-...",
    escalation_callback=on_escalate,
)
```

## CLI

### CI-native mode (recommended)

```bash
stitch ci                          # auto-detect platform, fix failed jobs
stitch ci --output json            # machine-readable output
stitch ci --platform gitlab        # override auto-detection
stitch ci --max-jobs 3             # limit jobs processed (default 5)
```

Platform is auto-detected from environment variables (`CI_PROJECT_ID` for GitLab, `GITHUB_REPOSITORY` for GitHub).

### Manual fix

For scripting or one-off fixes outside CI:

```bash
stitch fix \
  --platform gitlab \
  --project-id 42 \
  --pipeline-id 9999 \
  --job-id 1234 \
  --branch main
```

### Onboarding

```bash
stitch setup --repo . --platform gitlab    # generate .stitch.yml
stitch doctor --repo . --platform gitlab   # health check + diagnostics
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
    print(result.mr_url)   # PR URL if fixed

asyncio.run(main())
```

Works with GitHub too — use `GitHubAdapter` and `platform="github"`.

## Architecture

```
stitch_agent/
├── adapters/              # Platform integrations (GitLab, GitHub)
├── core/
│   ├── agent.py           # Fix loop + retry logic
│   ├── classifier.py      # Error detection (150+ patterns)
│   ├── fixer.py           # Claude-powered patch generation
│   ├── patch_validator.py # Programmatic patch safety checks
│   ├── pr_creator.py      # MR/PR creation
│   └── notifier.py        # Multi-channel notifications
├── onboarding/            # setup, doctor commands
├── models.py              # FixRequest, FixResult, ErrorType, ValidationConfig
├── history.py             # SQLite fix tracking
├── settings.py            # Environment configuration
└── config.py              # .stitch.yml parsing

runners/
├── cli.py                 # CLI entry point
└── ci_runner.py           # CI-native runner (fix, verify, retry)
```

## Contributing

Contributions are welcome. Please open an issue first to discuss what you'd like to change.

```bash
git clone https://github.com/g24r/stitch.git
cd stitch
pip install -e ".[dev]"
pytest
```

## License

MIT — see [LICENSE](LICENSE).
