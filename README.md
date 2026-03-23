<p align="center">
  <h1 align="center">stitch</h1>
  <p align="center"><strong>The AI that stitches your CI back together.</strong></p>
  <p align="center">
    Open-source AI agent that autonomously detects, diagnoses, and fixes CI pipeline failures.<br/>
    Platform-agnostic. Zero config. Lives inside your CI — no servers, no webhooks.
  </p>
</p>

<p align="center">
  <a href="https://pypi.org/project/stitch-agent/"><img src="https://img.shields.io/pypi/v/stitch-agent?style=flat-square" alt="PyPI"></a>
  <a href="https://pypi.org/project/stitch-agent/"><img src="https://img.shields.io/pypi/pyversions/stitch-agent?style=flat-square" alt="Python"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue?style=flat-square" alt="License"></a>
</p>

---

**Your CI pipeline just failed.** Again. A missing import. A linting rule. A type error in the code you didn't even touch. You context-switch, open the logs, squint at the error, make a one-line fix, push, wait. Rinse, repeat.

**stitch fixes that for you.** Add two jobs to your CI config and stitch will read the logs, classify the error, generate a fix, verify it passes CI, and open a PR — all automatically.

## How it works

```
Failed pipeline ──> stitch ──> fix branch ──> CI verifies ──> MR auto-created
```

1. **Fetch** — downloads job logs and the diff that triggered the failure
2. **Classify** — identifies the error type using 150+ patterns and confidence scoring
3. **Fix** — Claude generates a minimal, conservative patch for affected files
4. **Branch** — pushes the fix to a `stitch/fix-*` branch
5. **Verify** — CI runs on the fix branch to confirm the fix actually works
6. **PR** — if CI passes, stitch opens a Merge/Pull Request automatically

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
pip install stitch-agent
```

Requires **Python 3.12+**.

### Set credentials

Add these as CI/CD variables in your project:

```bash
STITCH_ANTHROPIC_API_KEY=sk-ant-...
STITCH_GITLAB_TOKEN=glpat-...      # or STITCH_GITHUB_TOKEN=ghp_...
```

### Add to your CI (30 seconds)

Copy one YAML snippet into your CI config. No server to deploy, no webhooks to configure.

**GitLab CI** — add to `.gitlab-ci.yml`:

```yaml
# Option A: after_script (granular — each job reports its own failure)

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

# Runs on stitch/fix-* branches: verify (CI passed → MR) or escalate (CI failed)
stitch-check:
  stage: .post
  when: always
  only:
    refs:
      - /^stitch\//
  script:
    - pip install stitch-agent
    - stitch ci
```

```yaml
# Option B: .post stage (catch-all — one job covers the whole pipeline)

stitch-fix:
  stage: .post
  when: on_failure
  except:
    refs:
      - /^stitch\//
  script:
    - pip install stitch-agent
    - stitch ci

stitch-check:
  stage: .post
  when: always
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
  # Fix: CI failed on a non-stitch branch → generate fix
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

  # Check: stitch/fix-* branch completed → verify (create MR) or escalate
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

That's it. When a pipeline fails, stitch generates a fix and pushes it to a `stitch/fix-*` branch. CI runs on that branch, and:
- **CI passes** → stitch creates the MR automatically
- **CI fails** → stitch escalates (the fix didn't work, human review needed)

> **Loop prevention:** The `except`/`only` rules (GitLab) and branch name conditions (GitHub) ensure stitch only fixes normal branches. The `stitch-check` job auto-detects whether to verify or escalate by checking for failed jobs in the pipeline. The `max_attempts` setting (default 3) caps retries via the API.

> **GitHub note:** `workflow_run` events fire 1-5 minutes after the triggering workflow completes. This is a GitHub platform limitation, not a stitch delay.

#### GitLab: `after_script` vs `.post` stage

| | `after_script` (Option A) | `.post` stage (Option B) |
|---|---|---|
| **Granularity** | Per-job — each job reports its own failure | Per-pipeline — one catch-all job |
| **`CI_JOB_ID`** | Points to the failed job itself | Points to the stitch job (needs API discovery) |
| **Setup** | YAML anchor on each job + shared check job | Two extra jobs |
| **Best for** | Repos where you want per-job fix PRs | Repos where you want a single fix PR per pipeline |

Both options include the `stitch-check` job that auto-detects whether to verify or escalate on fix branches.

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

On `stitch/fix-*` branches, `stitch ci` auto-detects the right action:
- **No failed jobs** → verify mode (creates MR)
- **Failed jobs** → escalate mode (reports fix didn't work)

## Alternative: CLI

For manual use or scripting outside CI (creates MR immediately, no two-phase flow):

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

Works with GitHub too — use `GitHubAdapter` and `platform="github"`. Self-hosted instances are supported via `STITCH_GITLAB_BASE_URL` / `STITCH_GITHUB_BASE_URL`.

## Onboarding

stitch includes commands to get you set up fast:

### `stitch setup` — auto-detect your project

```bash
stitch setup --repo . --platform gitlab
```

Scans your repo and generates a `.stitch.yml` config by detecting languages, linter, test runner, package manager, and CI provider.

### `stitch doctor` — health check

```bash
stitch doctor --repo . --platform gitlab --project-id 42
```

Runs diagnostic checks on Python version, API keys, platform connectivity, and permissions. Returns clear remediation steps for any issues found.

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

# Max fix attempts per branch (default 3)
STITCH_MAX_ATTEMPTS=3
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

max_attempts: 3

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

Run `stitch setup` to auto-generate this file.

## Notifications and escalation

When stitch can't auto-fix an error (low confidence, max attempts reached), it escalates. Configure notification channels in `.stitch.yml`:

```yaml
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

## CLI reference

```bash
# CI-native mode (recommended — run inside your CI pipeline)
stitch ci [--output json|text] [--platform gitlab|github] [--max-jobs 5]

# Fix a specific failed job (creates MR immediately)
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
```

**Exit codes:** `0` = success/fixed, `1` = error/escalated

## Architecture

```
stitch_agent/
├── adapters/           # Platform integrations (GitLab, GitHub)
├── core/
│   ├── agent.py        # Main fix loop
│   ├── classifier.py   # Error detection (150+ patterns)
│   ├── fixer.py        # Claude-powered patch generation
│   ├── pr_creator.py   # MR/PR creation
│   └── notifier.py     # Multi-channel notifications
├── onboarding/         # setup, doctor commands
├── models.py           # FixRequest, FixResult, ErrorType
├── history.py          # SQLite fix tracking
├── settings.py         # Environment configuration
└── config.py           # .stitch.yml parsing

runners/
├── cli.py              # CLI entry point
└── ci_runner.py        # CI-native runner (two-phase: fix + verify)
```

## License

MIT
