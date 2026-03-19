# Issue: Classifier returns UNKNOWN for unrecognized CI error patterns

## Symptom

`StitchAgent.fix()` returns `status=escalate` with `error_type=unknown` and reason:

> No recognizable error patterns found in job log

Confidence is set to 0.5 (hardcoded default). No fix is attempted.

## Root Cause

`stitch_agent/core/classifier.py` uses a static dict `_RULES` of regex patterns to score each `ErrorType`. When no pattern matches any line in the job log, `scores` stays empty and the classifier returns `ErrorType.UNKNOWN` immediately (line 114-120):

```python
if not scores:
    return ClassificationResult(
        error_type=ErrorType.UNKNOWN,
        confidence=0.5,
        summary="No recognizable error patterns found in job log",
        affected_files=[],
    )
```

The current `_RULES` covers: FORMAT, LINT, SIMPLE_TYPE, CONFIG_CI, COMPLEX_TYPE, TEST_CONTRACT, LOGIC_ERROR.

It does NOT cover common CI failures like:
- Shell script errors (`/bin/sh: command not found`, `exit code 1`, `returned a non-zero code`)
- Package manager failures (`apt-get: not found`, `apk add: error`, `npm install` failures)
- Docker/container build errors (`COPY failed`, `RUN returned non-zero exit code`)
- Missing environment variables or secrets (`variable not set`, `permission denied`)
- Network/connectivity failures (`curl: (6) Could not resolve host`)
- Generic job failures where the only signal is the exit code

## Observed Case

- **Project:** `x24labs/swagent/www`
- **Job:** `check`
- **Branch:** `main`
- **Context:** Commit `fix(ci): support both apt-get and apk for curl install` suggests the failure involves a shell command (curl install via apt-get or apk) that stitch can't pattern-match

## Expected Behavior

Either:
1. The classifier recognizes the error type from the job log and attempts a fix, OR
2. For UNKNOWN errors with clear actionable signals in the log, stitch still attempts a fix using the raw log + diff as context for the LLM (instead of bailing immediately)

## Proposed Fix Directions

### Option A — Extend `_RULES` with shell/infra patterns

Add a new `ErrorType` (e.g. `INFRA` or `BUILD`) or extend existing rules to catch:

```python
ErrorType.BUILD: [
    (re.compile(r"/bin/sh:.*not found", re.I), 1.0),
    (re.compile(r"returned a non-zero exit code", re.I), 0.9),
    (re.compile(r"command not found", re.I), 0.8),
    (re.compile(r"(apt-get|apk|yum|pip|npm|yarn|bun).*error", re.I), 0.9),
    (re.compile(r"No such file or directory", re.I), 0.7),
    (re.compile(r"curl:.*\(\d+\)", re.I), 0.8),
    (re.compile(r"COPY failed|RUN returned", re.I), 0.9),
],
```

### Option B — Fallback LLM classification for UNKNOWN

When no pattern matches, pass the raw job log to the LLM and ask it to classify and attempt a fix, instead of escalating immediately. Use a lower confidence threshold to flag the uncertainty.

### Option C — Improve classifier with LLM fallback

Keep regex as primary (fast, cheap). When `scores` is empty, run an LLM call to classify and extract the error summary, then continue the normal fix flow.

## Files to Modify

- `stitch_agent/core/classifier.py` — add patterns or LLM fallback
- `stitch_agent/models.py` — may need new `ErrorType` value if adding BUILD/INFRA
- `stitch_agent/core/agent.py` — may need changes if handling UNKNOWN differently
- `tests/` — add test cases for the new patterns

## Reproduction

Trigger the webhook with a GitLab pipeline that fails with a shell/package error and observe that stitch escalates with `error_type=unknown`. The job log for `x24labs/swagent/www` pipeline 3405 job 12118 is a concrete example.
