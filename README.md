# Stitch

**Run your CI jobs locally. Fix failures with AI.**

Stitch parses your CI configuration (GitLab CI, GitHub Actions), runs the jobs locally, and when something fails, delegates to an AI agent to fix it. Feedback in seconds, not minutes. No API keys, no config files.

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
  |- reports results with a live TUI
```

Uses your existing CLI subscription (Claude Pro, ChatGPT Plus). Zero config.

## Why Stitch

Other tools fix CI failures with AI. None of them work like Stitch.

| | Stitch | [Gitar](https://gitar.ai) | [Nx Cloud](https://nx.dev/docs/features/ci-features/self-healing-ci) | [Dagger + AI](https://dagger.io/blog/automate-your-ci-fixes-self-healing-pipelines-with-ai-agents/) |
|---|---|---|---|---|
| Uses your existing CI config | Yes | No | No | No |
| Runs jobs locally | Yes | Cloud only | Cloud only | Containers |
| Pluggable AI agent | Any CLI agent | Built-in only | Built-in only | Built-in only |
| Requires new infra | No | SaaS account | Nx monorepo | Dagger SDK |
| Pricing | Free (MIT) | From $20/user/mo | Nx Cloud plan | Free (OSS) |

**Gitar** and **Nx Cloud** are cloud platforms. They intercept failures in remote CI, fix them, and push commits to your PR. Powerful, but you need their platform, and every fix attempt costs a remote CI cycle.

**Dagger** can run locally, but you rewrite your pipelines in their SDK. It does not read your existing `.gitlab-ci.yml` or GitHub Actions workflows.

**Stitch** takes the CI config you already have, runs it on your machine in seconds, and hands failures to whichever AI agent CLI you prefer. No vendor lock-in, no rewrite, no waiting for remote runners. Just `stitch run claude` and move on.

## Claude Code skill (recommended)

Install the stitch skill so Claude Code validates your changes automatically before push, at the end of tasks, and when switching contexts.

```bash
ln -s /path/to/stitch/library/skills/stitch ~/.claude/skills/stitch
```

## Usage

```bash
stitch run claude                          # run all runnable jobs
stitch run claude --jobs lint,test         # only lint + test (prefix match)
stitch run claude --dry-run                # show what would run
stitch run claude --output json            # machine-readable output
stitch run claude --max-attempts 1         # report only, no fix attempts
stitch run claude --fail-fast              # stop after first failure
stitch run codex                           # use OpenAI Codex CLI instead
```

Agents:
- `claude` -- Claude Code CLI. Uses your Claude subscription.
- `codex` -- OpenAI Codex CLI. Uses your ChatGPT subscription.

## Watch mode

Re-runs your CI whenever you stop editing for a few seconds. Reports only, never invokes the agent.

```bash
stitch run claude --watch --jobs lint,test
stitch run claude --watch --debounce 5     # custom quiet window
```

## Job filtering

Stitch automatically skips jobs named `deploy*`, `publish*`, `release*`, `docker-build*`, `docker-push*`, `pages*`, `upload*`, `stitch*`.

Use `--jobs` to run only specific jobs:

```bash
stitch run claude --jobs lint,test,typecheck
```

Prefix matching: `--jobs test` matches `test`, `test:unit`, `test-e2e`, `test_fast`.

## Dependencies

Just two: `pyyaml` and `rich`. No API clients, no cloud SDKs.

## License

MIT
