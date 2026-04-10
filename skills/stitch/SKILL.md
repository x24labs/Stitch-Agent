---
name: stitch
version: 0.1.0
description: Validate code changes by running CI jobs locally before push. Delegates to the `stitch` CLI when installed, otherwise runs the CI jobs manually using Bash/Read/Edit. Use when the user wants to check their changes, run lint/test/typecheck, validate before pushing, or investigate a local CI failure. Triggers on mentions of stitch, CI, lint, typecheck, test, validate, pre-push, fix pipeline.
metadata:
  author: stitch-agent
  domain: devex
  triggers: stitch, run ci, validate, lint, typecheck, test, pre-push, check my changes, fix pipeline, ci failed
  role: specialist
  scope: implementation
  output-format: text
---

# stitch — Local CI Runner Skill

Run the project's CI jobs locally so the user gets feedback in seconds instead of waiting for remote CI, and fix failures automatically when possible.

This skill has two modes:

1. **Fast path (preferred):** the `stitch` CLI is installed on the system. You invoke it and parse its structured output. Stitch does the heavy lifting (parsing CI YAML, executing jobs with timeouts, locking, watch mode, filtering).
2. **Fallback:** `stitch` is NOT installed. You do the work manually using Bash, Read, Edit, Grep, and Glob.

Always prefer the fast path when available. The CLI is faster, more deterministic, and produces machine-readable output you can parse reliably.

## When to Use This Skill

### Explicit user request (reactive)

Trigger this skill when the user says anything like:

- "validate my changes" / "check my code" / "run the CI locally"
- "lint this" / "typecheck" / "run the tests"
- "fix the pipeline" / "CI is failing" / "something broke"
- "before push" / "pre-push check"
- "run stitch" / "stitch this"

### Proactive validation (MANDATORY)

You MUST run this skill automatically, without being asked, in these situations:

1. **Before any `git push`.** If the user asks you to push, or to "commit and push", or to create a PR/MR, run Stitch FIRST. If any job escalates, STOP and tell the user before pushing anything. Never push code that hasn't been validated locally.

2. **At the end of a non-trivial task.** When you finish implementing a feature, fixing a bug, refactoring, or any change that touches source files that a CI pipeline would check, run Stitch as your final step before declaring the work complete. Treat it as the equivalent of "checking your work" — you don't claim a task is done until the local CI is green.

3. **Before marking a TodoWrite task as `completed`** if that task involved code changes that would be checked by CI.

4. **When switching contexts** — if you've been working on one change and the user asks you to start something different, run Stitch first to make sure you're not leaving broken code behind.

### Do NOT trigger this skill for

- Generic code questions that don't involve running anything
- Remote CI investigation where the user hasn't checked out the branch locally
- One-off Bash commands the user can run themselves in < 5 seconds
- Trivial edits: single-character typo fixes, comment-only changes, README/doc-only changes, whitespace cleanup
- When the user explicitly says "don't run stitch" or "skip validation"
- When you already ran Stitch in this same turn and nothing has changed since

### How proactive runs should feel

When you run Stitch proactively, keep it lightweight:

- Announce it in one sentence: *"Running the local CI to verify."*
- Run with `--jobs` limited to what's relevant (`lint,typecheck,test`) to skip long jobs
- If it passes, a single line: *"✅ lint, typecheck, and test:unit passed — safe to push."*
- If it fails, show the error tail and ask: *"test:unit is failing with ... — want me to investigate?"*
- Never dump the full JSON unless asked
- Never loop "run → fix → run → fix" forever without checking in with the user after the first fix attempt

## Step 1 — Detect the Fast Path

Before doing anything else, check if `stitch` is installed:

```bash
which stitch
```

- If it returns a path → fast path is available, go to **Step 2A**.
- If it returns nothing → use the fallback, go to **Step 2B**.

## Step 2A — Fast Path: Use the `stitch` CLI

### Discover what would run

Always start with a dry run to show the user what Stitch detected:

```bash
stitch run claude --dry-run --repo <repo-path>
```

- `<repo-path>` defaults to the current working directory. Only pass `--repo` if the user explicitly named a different folder.
- Report the runnable vs. skipped jobs to the user in a short summary.

### Execute for real

After confirming (or if the user explicitly asked to run immediately):

```bash
stitch run claude --repo <repo-path> --output json
```

- Always use `--output json` so you can parse the result reliably.
- If the user named specific jobs, pass `--jobs lint,test` (comma-separated, prefix match supported).
- If the user explicitly asks for watch mode, pass `--watch` and explain that they'll need to keep the terminal open and press Ctrl+C to stop. Watch mode is NO-FIX by default: it only reports, never invokes a fixer.

### Interpret the JSON output

The JSON output has this shape:

```json
{
  "agent": "claude",
  "overall_status": "passed" | "failed",
  "jobs": [
    {
      "name": "lint",
      "status": "passed" | "escalated" | "skipped" | "not_run" | "failed",
      "attempts": 1,
      "driver": "claude" | null,
      "skip_reason": "matches skip pattern '^deploy'" | null,
      "error_log": "...truncated tail..."
    }
  ]
}
```

**How to report to the user:**

- `overall_status: passed` → one-line confirmation ("All 4 jobs passed ✅"). Don't dump the full JSON unless asked.
- `overall_status: failed` → list the jobs that failed, show their `error_log` tail (last 10-20 lines, not the full log), and ask if the user wants you to investigate and fix.
- Jobs with `status: skipped` → mention the count but don't list each one unless asked.

### When the user asks you to fix a failure

**Do NOT** re-run `stitch run claude` with `--max-attempts > 1` to let Stitch spawn another Claude Code instance — you ARE Claude Code. Doing that would spawn a nested session and waste subscription quota. Instead:

1. Take the `error_log` from the failed job
2. Use your native tools (Read, Grep, Edit) to investigate directly
3. Apply the fix to the files
4. Re-run only that job using `stitch run claude --jobs <job_name>` to verify

This is the crucial rule: **when the user is talking to you in Claude Code, YOU are the fixer.** Stitch is just the job runner.

### Common flags reference

| Flag | Purpose |
|------|---------|
| `--dry-run` | List jobs that would run, don't execute |
| `--output json` | Machine-readable output — always use this |
| `--jobs lint,test` | Allowlist of job names (prefix match) |
| `--max-attempts 1` | Disable the internal fix loop (you're fixing) |
| `--repo <path>` | Target another repository |
| `--fail-fast` | Stop after the first escalated job |
| `--watch` | Watch mode (no-fix by default, Ctrl+C to stop) |
| `--debounce <secs>` | Idle window for watch mode (default 3) |

### Important: always set --max-attempts 1

When you invoke stitch from within a Claude Code session, always pass `--max-attempts 1`. This prevents Stitch from spawning another `claude -p` subprocess for its own internal fix loop. You handle fixes manually with your native tools. Example:

```bash
stitch run claude --repo . --jobs lint,test --max-attempts 1 --output json
```

## Step 2B — Fallback: No `stitch` Installed

When `which stitch` returns nothing, you do the work manually.

### Detect the CI config

Check for these files in order:

1. `.gitlab-ci.yml` at the repo root
2. `.github/workflows/*.yml` or `*.yaml`
3. None of the above → fall back to stack detection (see below)

Use `Read` to load the YAML files. Parse them mentally (you don't need yaml library — just read the structure).

### Extract runnable jobs

For GitLab CI:
- Top-level keys that are dicts with a `script:` field are jobs
- Skip keys starting with `.` (templates)
- Skip reserved keys: `stages`, `default`, `image`, `variables`, `include`, `workflow`, `services`, `cache`, `before_script`, `after_script`
- For each job, the script is `before_script + script` (both normalized to list of strings)

For GitHub Actions:
- Look at `jobs:` → each key is a job
- Within each job, iterate `steps:` and collect `run:` commands (ignore `uses:`-only steps)

### Skip dangerous jobs by default

Never execute jobs whose name matches: `^deploy`, `^publish`, `^release`, `^docker-build`, `^docker-push`, `^pages`, `^upload`, `^stitch`.

Report them as skipped in your summary but don't run them.

### No CI config? Detect the stack

If neither GitLab nor GitHub CI files exist, detect the stack and run the standard tools:

| Stack | Detection | Default checks |
|-------|-----------|----------------|
| Python | `pyproject.toml` | `ruff check .`, `ruff format --check .`, `pyright` (if configured), `pytest -q` (if tests dir exists) |
| Node/TypeScript | `package.json` | `bun run lint`, `bun run typecheck`, `bun run test` (check scripts in package.json first) |
| Go | `go.mod` | `go vet ./...`, `gofmt -l .`, `go test ./...` |
| Rust | `Cargo.toml` | `cargo clippy`, `cargo fmt --check`, `cargo test` |

Prefer the project's actual scripts (`bun run lint`, `npm test`) over bare tool invocations when `package.json` defines them.

### Execute each job

Use the `Bash` tool. For each command in the job's script:

1. Run it with a reasonable timeout (60-120 seconds for lint, 300s for tests)
2. Capture the exit code
3. On non-zero exit, stop executing the rest of that job's script
4. Store the log tail for reporting

### Fix failures

Same rule as the fast path: you are the fixer. For each failed job:

1. Read the error log
2. Identify the root cause (use Grep/Read on the affected files)
3. Apply fixes with Edit
4. Re-run only that job's script to verify
5. Stop at 3 attempts per job; escalate to the user if you can't fix it

## Output Format

Always produce a short text summary to the user. Examples:

### All passing

```
✅ All 3 jobs passed
  • lint (0.8s)
  • typecheck (2.1s)
  • test:unit (5.4s)
```

### Partial failure

```
❌ 1 of 3 jobs failed
  ✅ lint
  ✅ typecheck
  ❌ test:unit — AssertionError in tests/test_users.py::test_create

I can investigate and fix this — want me to?
```

### After fixing

```
✅ Fixed test:unit
  • Modified: src/api/users.py (status code was 404 instead of 200)
  • Re-ran test:unit — passed
  • All jobs green. Safe to push.
```

Keep summaries concise. Don't dump JSON or full logs to the user unless they ask.

## Rules of Engagement

- **Never execute deploy/publish/release jobs**, even if the user asks, without explicit confirmation
- **Never run `git push`** automatically after fixing
- **Always use `--max-attempts 1`** when invoking stitch from within Claude Code (you are the fixer)
- **Always use `--output json`** in fast path for parseable results
- **Prefer the project's own scripts** (`bun run lint` over `eslint .`) when running commands manually
- **One job at a time when fixing** — don't run the full suite after every edit, just re-run the failing job
- **Stop at 3 attempts per job** — escalate to the user rather than looping indefinitely
- **Respect `.stitch.yml`** — if the user has one, its `run.include`, `run.skip`, `run.only` take precedence
- **Never commit** changes after fixing unless explicitly asked

## Example Interactions

### Example 1 — Quick pre-push check

```
User: check my changes before I push

You:
  1. which stitch → /home/user/.local/bin/stitch
  2. stitch run claude --dry-run --repo . → shows 4 runnable jobs
  3. "I'll run lint, typecheck, and test:unit (skipping deploy)."
  4. stitch run claude --jobs lint,typecheck,test --max-attempts 1 --output json
  5. Parse result: all passed.
  6. "✅ All 3 jobs passed. Safe to push."
```

### Example 2 — Test failure, user wants a fix

```
User: my tests are broken, can you fix them?

You:
  1. which stitch → found
  2. stitch run claude --jobs test --max-attempts 1 --output json
  3. Parse result: test:unit escalated, error_log mentions AssertionError in test_users.py
  4. Read tests/test_users.py and src/api/users.py
  5. Identify root cause: handler returns 404 on duplicate email but test expects 409
  6. Edit src/api/users.py to return 409
  7. stitch run claude --jobs test --max-attempts 1 --output json → passed
  8. "Fixed. The duplicate-email handler was returning 404 instead of 409 — aligned it with the test."
```

### Example 3 — No stitch installed, Python project

```
User: run lint and tests

You:
  1. which stitch → nothing
  2. Check .gitlab-ci.yml → not found
  3. Check .github/workflows/ → empty
  4. Check pyproject.toml → exists, Python project
  5. Run: ruff check . → passes
  6. Run: pyright → passes (config found in pyproject.toml)
  7. Run: pytest -q → 42 passed
  8. "✅ ruff, pyright, and pytest all passed (42 tests)."
```

### Example 4 — Proactive run before push

```
User: commit this and push it

You:
  "Running the local CI to verify before push."
  
  Run: stitch run claude --jobs lint,typecheck,test --max-attempts 1 --output json
  Parse result: all passed.
  
  "✅ lint, typecheck, test:unit all green. Committing and pushing now."
  
  Then proceed with the commit + push.
```

### Example 5 — Proactive run catches a regression before push

```
User: ok finish that and push it

You:
  "Running local CI before push."
  
  Run: stitch run claude --jobs lint,test --max-attempts 1 --output json
  Parse result: test:unit escalated (error in test_users.py).
  
  "⚠️  test:unit is failing:
  
    AssertionError: expected 200, got 404
    tests/test_users.py::test_create_user
  
  I introduced a regression in my last edit. Want me to investigate before
  we push, or push anyway?"
  
  Wait for the user's decision. Default is to fix, not push.
```

### Example 6 — End-of-task self-validation

```
User: implement the /users/:id DELETE endpoint

You:
  (implements the endpoint, writes the test, updates the router)
  
  "Implementation done. Running local CI to verify."
  
  Run: stitch run claude --jobs lint,typecheck,test --max-attempts 1 --output json
  All passed.
  
  "✅ DELETE /users/:id implemented and verified:
    • Added src/api/users.py:delete_user
    • Added tests/test_users.py::test_delete_user
    • Updated src/api/router.py
    • lint, typecheck, test:unit all green"
```

### Example 7 — User asks for watch mode

```
User: watch my files and tell me when something breaks

You:
  "I'll start stitch in watch mode. It'll run your CI jobs every time your
  files settle after changes (3s debounce). Press Ctrl+C to stop.
  
  Note: watch mode is no-fix — I'll only report failures, not fix them
  automatically. When you want me to fix something, ask me directly."
  
  Then run: stitch run claude --repo . --watch --jobs lint,test
  (this will run until the user stops it)
```

## Troubleshooting

| Symptom | Likely cause | What to do |
|---------|--------------|------------|
| `stitch: command not found` | CLI not in PATH | Use the fallback path |
| `No CI configuration found` | Missing .gitlab-ci.yml / workflows | Fall back to stack detection |
| `Another stitch instance is running (pid ...)` | Lock file from a prior watch session | If safe, delete `.stitch.lock` in the repo root |
| JSON output is empty or missing | Stitch hit an error before running jobs | Re-run without `--output json` to see the error message |
| Stitch reports "escalated" but you didn't see an error | Job exit code was non-zero but log is empty | Check the job's script manually with Bash |

## Relationship to the `stitch` CLI

This skill is a thin wrapper over the `stitch` CLI (`pip install stitch-agent` or from the repo). The CLI handles:

- CI YAML parsing (GitLab + GitHub)
- Sequential job execution with timeouts
- Filesystem watcher (watch mode)
- Lock file for concurrent-run safety
- Machine-readable reports

This skill handles:

- Natural-language intent → correct CLI invocation
- Parsing the JSON output into a human-readable summary
- Deciding when to fix directly vs. when to escalate to the user
- Fallback execution when the CLI isn't installed

The CLI is the motor; this skill is the UX when the user is inside Claude Code.
