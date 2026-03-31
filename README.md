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
- **Cost-transparent** — every fix reports exact token usage and real cost in USD. Simple errors use a fast, cheap model; complex errors escalate to a heavier model. You always know what you're spending.
- **Model-agnostic** — powered by [OpenRouter](https://openrouter.ai), giving you access to 200+ models. Choose the best price/performance ratio for your needs.
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
STITCH_OPENROUTER_API_KEY=sk-or-...    # Get one at https://openrouter.ai/keys
STITCH_GITLAB_TOKEN=glpat-...          # or STITCH_GITHUB_TOKEN for GitHub
```

> **Important:** In GitLab, uncheck **"Protect variable"** for both variables. stitch pushes fixes to `stitch/fix-*` branches, which are not protected by default. Protected variables are only injected into protected branches, so stitch won't be able to authenticate on fix branches if the variables are protected.

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
          STITCH_OPENROUTER_API_KEY: ${{ secrets.STITCH_OPENROUTER_API_KEY }}
          STITCH_GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}

  check:
    if: startsWith(github.event.workflow_run.head_branch, 'stitch/')
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: pip install stitch-agent
      - run: stitch ci
        env:
          STITCH_OPENROUTER_API_KEY: ${{ secrets.STITCH_OPENROUTER_API_KEY }}
          STITCH_GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

</details>

**Done.** When a pipeline fails, stitch pushes a fix to a `stitch/fix-*` branch. CI runs on that branch:

- **CI passes** → stitch creates the PR automatically
- **CI fails** → stitch retries on the same branch (escalating to the heavy model after 2 attempts)
- **All retries exhausted** → stitch notifies your team for human review

> **Loop prevention:** The `except`/`only` rules (GitLab) and branch conditions (GitHub) ensure stitch only fixes normal branches. `max_attempts` (default 3) caps retries.

## How it works

| Phase | Trigger | What happens |
|-------|---------|-------------|
| **Fix** | CI fails on your branch | Generates fix, pushes to `stitch/fix-*` branch |
| **Retry** | CI fails on `stitch/fix-*` | Retries on the same branch, escalating model after 2 attempts |
| **Verify** | CI passes on `stitch/fix-*` | Creates PR targeting your original branch |
| **Exhaust** | Max retries reached | Notifies team via Slack/webhook |

### Cost visibility

Every stitch run reports exactly what it consumed — no surprises on your OpenRouter bill:

```
  ┌─────────────────────────────────────────┐
  │  Stitch Agent v0.6.2                    │
  │  The AI that stitches your CI back      │
  │  together.                              │
  ├─────────────────────────────────────────┤
  │  Platform:  gitlab                      │
  │  Mode:      FIX                         │
  │  Branch:    main                        │
  │  Pipeline:  3692                        │
  └─────────────────────────────────────────┘

✅ [lint] Fixed
   Reason: Removed unused import os
   Branch: stitch/fix-3692
   Tokens: 1,234 in / 567 out (1,801 total) — $0.0003
```

Token counts come directly from the API response (zero overhead). Cost is fetched from OpenRouter's generation API — the actual amount charged, not an estimate.

JSON output includes the full breakdown:

```json
{
  "status": "fixed",
  "usage": {
    "prompt_tokens": 1234,
    "completion_tokens": 567,
    "total_tokens": 1801,
    "cost_usd": 0.000312
  }
}
```

### Error classification

stitch classifies errors to choose the right model and strategy:

| Type | Model tier | Examples |
|------|-----------|---------|
| `lint` | light | Unused imports, missing semicolons, style violations |
| `format` | light | Indentation, trailing whitespace, line length |
| `simple_type` | light | Missing type annotations, basic type mismatches |
| `config_ci` | light | YAML syntax, missing CI variables, stage ordering |
| `build` | light | Missing dependencies, import errors, module resolution |
| `complex_type` | heavy | Generic type inference, conditional types, overloads |
| `test_contract` | heavy | Broken test assertions, mock mismatches, fixture errors |
| `logic_error` | heavy | Off-by-one errors, incorrect conditions, edge cases |
| `unknown` | heavy | Unclassified errors — heavier model handles the ambiguity |

Format and lint errors use a **fast-path**: file contents are pre-fetched and included in the prompt, skipping the tool-use investigation loop entirely (single API call instead of up to 15 rounds).

### Model selection

stitch uses [OpenRouter](https://openrouter.ai) to access any LLM. Three models are configured independently:

| Role | Default | Used for |
|------|---------|---------|
| **classifier** | `google/gemini-2.5-flash-lite` | Error classification from CI logs |
| **light** | `google/gemini-2.5-flash-lite` | Simple fixes (lint, format, types, CI config, build) |
| **heavy** | `google/gemini-2.5-flash` | Complex fixes (type inference, test failures, logic errors) |

Override any model via `.stitch.yml`:

```yaml
models:
  classifier: deepseek/deepseek-chat-v3.1
  light: qwen/qwen3.5-9b
  heavy: x-ai/grok-4.1-fast
```

Browse available models at [openrouter.ai/models](https://openrouter.ai/models).

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
# Required — LLM access via OpenRouter
STITCH_OPENROUTER_API_KEY=sk-or-...

# Platform credentials (at least one)
STITCH_GITLAB_TOKEN=glpat-...
STITCH_GITHUB_TOKEN=ghp_...

# Self-hosted instances
STITCH_GITLAB_BASE_URL=https://gitlab.example.com
STITCH_GITHUB_BASE_URL=https://github.example.com/api/v3

# Custom OpenRouter endpoint (optional)
STITCH_OPENROUTER_BASE_URL=https://openrouter.ai/api/v1

# Tuning (optional)
STITCH_HAIKU_CONFIDENCE_THRESHOLD=0.80    # threshold for light model types
STITCH_SONNET_CONFIDENCE_THRESHOLD=0.40   # threshold for heavy model types
STITCH_MAX_ATTEMPTS=3                     # default
```

### Per-repo config

Place a `.stitch.yml` in your repo root to customize behavior. Run `stitch setup` to auto-generate it.

```yaml
languages: [python, typescript]
linter: ruff
test_runner: pytest
package_manager: pip

# Choose your models (any OpenRouter model ID)
models:
  classifier: google/gemini-2.5-flash-lite
  light: google/gemini-2.5-flash-lite
  heavy: google/gemini-2.5-flash

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
    api_key="sk-or-...",
    base_url="https://openrouter.ai/api/v1",
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
stitch ci -v                       # verbose debug output
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
agent = StitchAgent(
    adapter=adapter,
    api_key="sk-or-...",
    base_url="https://openrouter.ai/api/v1",
)

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
    print(result.status)       # 'fixed' | 'escalate' | 'error'
    print(result.mr_url)       # PR URL if fixed
    print(result.usage.cost_usd)  # actual cost in USD

asyncio.run(main())
```

Works with GitHub too — use `GitHubAdapter` and `platform="github"`.

## Architecture

```
stitch_agent/
├── adapters/              # Platform integrations (GitLab, GitHub)
├── core/
│   ├── agent.py           # Fix loop + retry logic
│   ├── classifier.py      # Error detection (LLM + 150+ regex patterns)
│   ├── fixer.py           # LLM-powered patch generation (agentic + fast-path)
│   ├── patch_validator.py # Programmatic patch safety checks
│   ├── pr_creator.py      # MR/PR creation
│   └── notifier.py        # Multi-channel notifications
├── onboarding/            # setup, doctor commands
├── models.py              # FixRequest, FixResult, ErrorType, ModelConfig
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
git clone https://git.g24r.com/x24labs/stitch/library.git
cd stitch
pip install -e ".[dev]"
pytest
```

## License

MIT — see [LICENSE](LICENSE).
