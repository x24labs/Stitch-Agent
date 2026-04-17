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

**Prerequisite**: an agent CLI installed and logged in. Either [Claude Code](https://docs.claude.com/en/docs/claude-code/overview) (`npm i -g @anthropic-ai/claude-code`) or [OpenAI Codex CLI](https://github.com/openai/codex) (`npm i -g @openai/codex`). Stitch shells out to whichever you have.

```bash
npx stitch-agent doctor                 # check your setup
npx stitch-agent run claude --dry-run   # see what would run
npx stitch-agent run claude             # run + fix failures
```

Or install globally:

```bash
npm install -g stitch-agent
stitch run claude
```

## See it in action

<p align="center">
  <img src="https://stitch-agent.dev/tui/stitch-run.gif" alt="Animated terminal recording of stitch run claude showing the TUI with pipeline stepper, job table, and agent driver panel" width="720" />
</p>

**Live run.** One command. Stitch parses your CI config, runs verify jobs locally, hands failures to Claude Code or Codex, and re-verifies the fix. All of it streams into a single terminal window: pipeline stepper at the top, live job table in the middle, driver panel showing what the agent is actually doing at the bottom.

<p align="center">
  <img src="https://stitch-agent.dev/tui/stitch-history.jpg" alt="Terminal screenshot of stitch history showing STITCH ASCII logo, agent info, and a table of recent runs with pass, fixed, and ongoing states" width="720" />
</p>

**Run history.** Every run is recorded. PASS streaks show verify jobs that keep working on their own, FIXED entries record when the agent resolved a failure (with attempt count and commit), and escalations the agent could not resolve are surfaced, not hidden. No dashboard, no account. Just a file on your machine.

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

## Claude Code integration

Stitch ships with a Claude Code skill. Install it once and Claude validates your code on its own, without you asking.

The skill is wired to four moments where broken code tends to leak out:

- **Before every push.** Ask Claude to push, commit, or open a PR, and Stitch runs first. If anything fails, Claude stops and tells you before the commit leaves your machine.
- **At the end of a task.** When Claude finishes implementing a feature, fixing a bug, or refactoring, it runs Stitch as the last step before declaring the work done.
- **Before marking a todo complete.** If a TodoWrite item touches code a pipeline would check, Claude runs Stitch first.
- **When switching context.** If you pivot to a different change, Claude runs Stitch on the previous one so nothing broken is left behind.

It also auto-triggers on natural-language mentions like *"validate this"*, *"check my code"*, *"run the CI"*, *"fix the pipeline"*, *"CI is failing"*, *"pre-push check"*. You can still invoke it explicitly with `/stitch`.

Install from a local clone:

```bash
ln -s "$(pwd)/skills/stitch" ~/.claude/skills/stitch
```

Install from a global npm install:

```bash
ln -s "$(npm root -g)/stitch-agent/skills/stitch" ~/.claude/skills/stitch
```

After that, open Claude Code in any repo with a CI config and start working. You should not need to mention Stitch by name again.

## Features

What Stitch actually does, end to end:

- **Three CI platforms out of the box.** Parses `.gitlab-ci.yml`, `.github/workflows/*.yml`, and `bitbucket-pipelines.yml` from the same CLI. No rewrite, no wrapper, no YAML translation layer.
- **Parallel execution by design.** Jobs run concurrently during the detection phase, so your wall-clock time is `max(job_i)` instead of `sum(job_i)`. Your 4-job pipeline takes as long as your slowest job, not all four added together.
- **Fail-fast when you want it.** `--fail-fast` cancels in-flight jobs the moment one fails, then hands that single failure to the agent to fix. No waiting for the other 3 jobs to finish before the fix-loop can start.
- **Batch AI fixes.** When multiple jobs fail, Stitch sends *all* the failures to the agent in a single call. The agent sees the failures together, which means one fix can resolve correlated errors (a broken type that breaks both lint and typecheck, a missing import that breaks tests and build). One invocation, one edit pass, fewer tokens, fewer seconds.
- **Re-verify, don't just re-report.** After the agent edits, Stitch re-runs the previously failed jobs to prove the fix actually worked. Up to `max_attempts` per job, configurable per repo.
- **Pluggable agent CLI.** Works with Claude Code, Codex, and whatever you have a subscription for. No Anthropic or OpenAI API key required; Stitch just shells out to the agent you already pay for.
- **Native Claude Code skill.** Ships with a Claude Code skill. Install it once and Claude validates your code on its own: before every push, at the end of a task, before marking a todo complete, and when switching context. No command, no prompt, no flag. See [Claude Code integration](#claude-code-integration).
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
| Native Claude Code integration | Yes, ships with a skill | No | No | No |
| Pricing | Free (MIT) | From $20/user/mo | Nx Cloud plan | Free (OSS) |

**Gitar** and **Nx Cloud** are cloud platforms. They intercept failures in remote CI, fix them, and push commits to your PR. Powerful, but you need their platform, and every fix attempt costs a remote CI cycle.

**Dagger** can run locally, but you rewrite your pipelines in their SDK. It does not read your existing `.gitlab-ci.yml` or GitHub Actions workflows.

**Stitch** takes the CI config you already have, runs it on your machine in seconds, and hands failures to whichever AI agent CLI you prefer. No vendor lock-in, no rewrite, no waiting for remote runners.

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
stitch doctor                              # diagnose your setup
stitch doctor --output json                # machine-readable diagnostics
stitch history                             # show recent runs (streak-compacted)
stitch history --job lint --limit 20       # filter and limit
stitch history --output json               # machine-readable
```

Agents:
- `claude` - Claude Code CLI. Uses your Claude subscription.
- `codex` - OpenAI Codex CLI. Uses your ChatGPT subscription.

Debugging:
- `STITCH_DEBUG=1` - print the stack trace when Stitch exits on an unexpected error.

## Watch mode

Re-runs your CI whenever you stop editing for a few seconds. Reports only, never invokes the agent.

```bash
stitch run claude --watch --jobs lint,test
stitch run claude --watch --debounce 5     # custom quiet window
```

Between runs, press `Enter` (or `r`) to re-run immediately without waiting for a file change. During a run, `Ctrl+C` aborts the current run and returns to watch idle; press `q` to exit fully.

## Job filtering

Stitch automatically skips jobs named `deploy*`, `publish*`, `release*`, `docker-build*`, `docker-push*`, `pages*`, `upload*`, `stitch*`.

Use `--jobs` to run only specific jobs:

```bash
stitch run claude --jobs lint,test,typecheck
```

Prefix matching: `--jobs test` matches `test`, `test:unit`, `test-e2e`, `test_fast`.

## History

Every run records its outcome to `.stitch/history.jsonl` (and a small `.stitch/history-head.json` index). Consecutive identical results for the same job are compacted into a single entry with a `runs` counter, so 100 green runs cost one line, not a hundred. A streak only flushes when the result changes (status, attempts, or first error line). Fixes are never collapsed: every successful AI fix gets its own entry with the commit SHA.

The history files are safe to commit. They sync naturally across machines with the rest of the repo. The log rotates after 5,000 entries (one backup kept).

```bash
stitch history                # latest 50 streaks + ongoing
stitch history --job test     # only one job
stitch history --output json  # for scripts
```

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
