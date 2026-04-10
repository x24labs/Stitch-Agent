# stitch

**Run your CI jobs locally. Fix failures with AI.**

Stitch parses your CI configuration (GitLab CI, GitHub Actions), runs the jobs locally, and when something fails, delegates to an AI agent to fix it. Feedback in seconds, not minutes. Zero API cost when tests pass.

## Quick start

```bash
pip install stitch-agent

cd your-repo

stitch run claude --dry-run       # see what would run
stitch run claude                 # run + fix failures
```

## How it works

```
stitch run claude
  |
  |- parses .gitlab-ci.yml / .github/workflows/*.yml
  |- filters jobs (skips deploy, publish, docker-build)
  |- runs each job locally (subprocess with timeout)
  |
  |- job passes? next job
  |- job fails?
  |    |- spawns `claude -p` with the error log
  |    |- Claude Code investigates and edits files
  |    |- re-runs the job to verify
  |    |- repeat up to --max-attempts
  |
  |- reports results (text or JSON)
```

No API keys needed for `claude` or `codex` drivers. They use your existing CLI subscription.

## Claude Code skill (recommended)

For the best experience, install the stitch skill into Claude Code. This makes validation automatic: Claude runs your CI before push, at the end of tasks, and when switching contexts.

```bash
# Copy from the stitch repo
cp -r skills/stitch ~/.claude/skills/stitch

# Or symlink
ln -s /path/to/stitch/library/skills/stitch ~/.claude/skills/stitch
```

Once installed, Claude Code will proactively validate your changes before pushing.

## Commands

### `stitch run <agent>`

Run CI jobs locally with an AI fix loop.

```bash
stitch run claude                          # run all runnable jobs
stitch run claude --jobs lint,test         # only lint + test (prefix match)
stitch run claude --dry-run                # show what would run
stitch run claude --output json            # machine-readable output
stitch run claude --max-attempts 1         # report only, no fix attempts
stitch run claude --fail-fast              # stop after first failure
stitch run claude --watch                  # re-run on file changes (Ctrl+C to stop)
stitch run claude --watch --debounce 5     # wait 5s of quiet before re-running
```

Agents:
- `claude` -- spawns Claude Code CLI (`claude -p`). Uses your Claude subscription.
- `codex` -- spawns OpenAI Codex CLI. Uses your ChatGPT subscription.
- `api` -- direct LLM API call via OpenRouter. Requires `STITCH_OPENROUTER_API_KEY`.

### `stitch setup`

Bootstrap a `.stitch.yml` configuration file by detecting your project's stack.

```bash
stitch setup --repo .
stitch setup --platform github --json
```

### `stitch doctor`

Check your environment: API keys, CI config, provider connectivity.

```bash
stitch doctor --repo .
stitch doctor --platform gitlab --project-id my-group/my-project --json
```

## Configuration

Stitch reads `.stitch.yml` from your repo root:

```yaml
languages:
  - python
linter: ruff
test_runner: pytest
package_manager: uv
conventions:
  - conventional commits
```

### Job filtering

Control which jobs run locally via `.stitch.yml`:

```yaml
run:
  skip:
    - ^slow-       # regex patterns to skip
  include:
    - docker-build # force-include a normally skipped job
  only:
    - lint          # if set, ONLY these run (prefix match)
    - test
```

Default skip list: `deploy*`, `publish*`, `release*`, `docker-build*`, `docker-push*`, `pages*`, `upload*`, `stitch*`.

## Watch mode

Leave stitch running in a terminal. It re-runs your CI whenever you stop editing for a few seconds.

```bash
stitch run claude --watch --jobs lint,test
```

Watch mode is no-fix by default: it only reports pass/fail, never invokes the agent. This avoids conflicts with your editor or other AI tools.

## Environment variables

| Variable | Purpose |
|----------|---------|
| `STITCH_OPENROUTER_API_KEY` | API key for the `api` driver (OpenRouter) |
| `STITCH_GITLAB_TOKEN` | GitLab token for `stitch doctor` connectivity checks |
| `STITCH_GITHUB_TOKEN` | GitHub token for `stitch doctor` connectivity checks |

## License

MIT
