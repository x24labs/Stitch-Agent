<p align="center">
  <img src="assets/stitch-logo.png" alt="Stitch" width="160" />
</p>

<h1 align="center">Stitch</h1>

<p align="center">
  <strong>Run your CI locally. Fix failures with AI.</strong>
</p>

<p align="center">
  <a href="https://www.npmjs.com/package/stitch-agent"><img src="https://img.shields.io/npm/v/stitch-agent?color=blue&label=npm" alt="npm version" /></a>
  <a href="https://www.npmjs.com/package/stitch-agent"><img src="https://img.shields.io/node/v/stitch-agent" alt="node version" /></a>
  <a href="https://github.com/x24labs/stitch/blob/main/LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="license" /></a>
</p>

---

Stitch parses your CI configuration (GitLab CI, GitHub Actions, Bitbucket Pipelines), runs the jobs on your machine, and when something fails, hands the error to an AI agent that fixes it. Feedback in seconds, not minutes. No API keys. Zero config by default, with an optional `.stitch.yml` for per-repo defaults.

```
                                                         +*+++++++++-         .+%
                                                      .=+---=====+++++:       ::
                         .                          .=+---------.  :-:=++++-:+==--:.
                      :+*++=                       =+-------*%%%#++**#=====-+*+:....
                      .=+++++=                   +*+=-----#%%%%%%%%%%%%     =-
                         =++++++:              +***++++++*%%%%%%%%%%%%     -*:
                           =++++++-         .=****++++++++%%%%%%%%%%#.    -#-
                             .=+++++:.   .:+****++++++++++%%%%%%%%%%#.   .+-
                               .=++++++:-=+***+++++++++++*%%%%%%%%%%#.  .==
                                 .-+=---+**++++++++++++*%%%%%%%%%%%%%.  :*
                                     :+*++++++++++++*#%%%%%%%%%%%%%%:  .#
                                    ++++++++++*###%%%%%%%%%%%%%%%%=    #
                                  =*+==+#######%%%%%%%%%%%%%%%%%*:    #
                                ::      .*#%%%%%%%%%%%%%%%%%%%#-     +
                                           :===%%%%%%%%%*====-      -
                                             ..   :..
                                                .    ..
```

## Quick start

```bash
npx stitch-agent run claude --dry-run   # see what would run
npx stitch-agent run claude             # run + fix failures
```

Or install globally:

```bash
npm install -g stitch-agent
stitch run claude
```

## How it works

```
stitch run claude
  |
  |- parses .gitlab-ci.yml / .github/workflows/*.yml / bitbucket-pipelines.yml
  |- filters jobs (skips deploy, publish, docker-build)
  |- runs each job locally (subprocess with timeout)
  |
  |- job passes? next job
  |- job fails?
  |    |- spawns the AI agent CLI with the error log
  |    |- agent investigates and edits files
  |    |- re-runs the job to verify the fix
  |    |- repeat up to --max-attempts
  |
  |- reports results with a live TUI
```

Uses your existing CLI subscription (Claude Pro, ChatGPT Plus). Zero config by default; optional `.stitch.yml` for per-repo defaults (see [Configuration](#configuration-optional)).

## Features

What Stitch actually does, end to end:

- **Three CI platforms out of the box.** Parses `.gitlab-ci.yml`, `.github/workflows/*.yml`, and `bitbucket-pipelines.yml` from the same CLI. No rewrite, no wrapper, no YAML translation layer.
- **Parallel execution by design.** Jobs run concurrently during the detection phase, so your wall-clock time is `max(job_i)` instead of `sum(job_i)`. Your 4-job pipeline takes as long as your slowest job, not all four added together.
- **Fail-fast when you want it.** `--fail-fast` cancels in-flight jobs the moment one fails, then hands that single failure to the agent to fix. No waiting for the other 3 jobs to finish before the fix-loop can start.
- **Batch AI fixes.** When multiple jobs fail, Stitch sends *all* the failures to the agent in a single call. The agent sees the failures together, which means one fix can resolve correlated errors (a broken type that breaks both lint and typecheck, a missing import that breaks tests and build). One invocation, one edit pass, fewer tokens, fewer seconds.
- **Re-verify, don't just re-report.** After the agent edits, Stitch re-runs the previously failed jobs to prove the fix actually worked. Up to `max_attempts` per job, configurable per repo.
- **Pluggable agent CLI.** Works with Claude Code, Codex, and whatever you have a subscription for. No Anthropic or OpenAI API key required; Stitch just shells out to the agent you already pay for.
- **Live TUI.** OpenTUI-based terminal interface with per-job progress, logs on demand, and stage grouping. Renders on any terminal; no ANSI flicker.
- **Auto commit and push on green.** When the fix-loop lands, Stitch commits the change with a sensible message and pushes to the remote, so the CI fix closes the loop on its own. Opt out with `--no-push` or `push: false` in `.stitch.yml`.
- **Watch mode.** Re-runs your CI on every save, with a debounce you control. Reports only, never invokes the agent.
- **Zero-install-friendly.** `npx stitch-agent run claude` works without installing anything globally. No Python, no Docker, no account.
- **JSON output for pipelines.** `--output json` gives you a machine-readable report if you want to wrap Stitch inside a larger tool.
- **Prefix-match job filtering.** `--jobs test` picks up `test`, `test:unit`, `test-e2e`, `test_fast` with separator awareness, so you do not need to list every variant.
- **Optional per-repo config.** `.stitch.yml` sets defaults (agent, max attempts, push policy, include/exclude job lists). CLI flags still win. When the file is absent, behavior is unchanged.

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

**Stitch** takes the CI config you already have, runs it on your machine in seconds, and hands failures to whichever AI agent CLI you prefer. No vendor lock-in, no rewrite, no waiting for remote runners.

## Claude Code skill (recommended)

Install the stitch skill so Claude Code validates your changes automatically before push, at the end of tasks, and when switching contexts.

```bash
ln -s /path/to/stitch/library/skills/stitch ~/.claude/skills/stitch
```

## Usage

```bash
stitch run claude                          # run all runnable jobs
stitch run                                 # agent from .stitch.yml (else: claude)
stitch run claude --jobs lint,test         # only lint + test (prefix match)
stitch run claude --dry-run                # show what would run
stitch run claude --output json            # machine-readable output
stitch run claude --max-attempts 1         # report only, no fix attempts
stitch run claude --fail-fast              # stop after first failure
stitch run codex                           # use OpenAI Codex CLI instead
```

Agents:
- `claude` - Claude Code CLI. Uses your Claude subscription.
- `codex` - OpenAI Codex CLI. Uses your ChatGPT subscription.

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

## Configuration (optional)

Drop a `.stitch.yml` (or `.stitch.yaml`) at your repo root to set per-repo defaults. Every field is optional, and CLI flags always win over the file (flag > config > default). If the file is absent, behavior is identical to today.

```yaml
agent: claude              # claude | codex
max_attempts: 3
push: true                 # auto-push after a successful fix
jobs:
  include: [lint, test, typecheck]   # prefix-match allowlist
  exclude: [deploy, publish]         # prefix-match blocklist
classification: llm        # llm | none  (none = run every parsed job)
```

See [`.stitch.example.yml`](./.stitch.example.yml) for the full annotated template. Unknown fields are rejected, so typos surface immediately.

## License

MIT
