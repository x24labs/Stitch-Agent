import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { detectPlatform } from "../core/ci-detect.js";
import { CIParseError, parseCIConfig } from "../core/ci-parser.js";
import { ConfigError, type StitchConfig, loadConfig } from "../core/config.js";
import { applyFilter, classifyWithLLM, loadCache, saveCache } from "../core/filter.js";
import { commit, push, snapshot } from "../core/git.js";
import { recordRun } from "../core/history.js";
import type { CIJob, CommitPushReason, GitSnapshot } from "../core/models.js";
import { type RunReport, isCommittable, isPushable } from "../core/models.js";
import { Runner, type RunnerConfig } from "../core/runner.js";
import {
  AbortedError,
  LockAcquireError,
  StitchLock,
  type WatchConfig,
  waitForChangeThenIdle,
} from "../core/watcher.js";
import { ClaudeCodeDriver } from "../drivers/claude-code.js";
import { CodexDriver } from "../drivers/codex.js";
import type { AgentDriver } from "../drivers/types.js";

interface RunOptions {
  agent: string;
  repo: string;
  maxAttempts?: number;
  output: string;
  dryRun: boolean;
  failFast: boolean;
  jobs?: string;
  push?: boolean;
  watch: boolean;
  debounce: number;
}

interface ResolvedOptions {
  agent: string;
  repo: string;
  maxAttempts: number;
  output: string;
  dryRun: boolean;
  failFast: boolean;
  jobs?: string;
  push: boolean;
  watch: boolean;
  debounce: number;
  excludeJobs: string[] | null;
  classification: "llm" | "none";
}

function resolveOptions(opts: RunOptions, cfg: StitchConfig | null): ResolvedOptions {
  const include = opts.jobs ?? cfg?.jobs?.include?.join(",");
  return {
    agent: opts.agent,
    repo: opts.repo,
    maxAttempts: opts.maxAttempts ?? cfg?.max_attempts ?? 3,
    output: opts.output,
    dryRun: opts.dryRun,
    failFast: opts.failFast,
    jobs: include,
    push: opts.push ?? cfg?.push ?? true,
    watch: opts.watch,
    debounce: opts.debounce,
    excludeJobs: cfg?.jobs?.exclude ?? null,
    classification: cfg?.classification ?? "llm",
  };
}

const VALID_AGENTS = ["claude", "codex"];

function buildDriver(agent: string): AgentDriver | null {
  if (agent === "claude") return new ClaudeCodeDriver();
  if (agent === "codex") return new CodexDriver();
  return null;
}

interface CommitPushResult {
  sha: string | null;
  pushed: boolean;
  reason: CommitPushReason;
}

function autoCommitPush(
  repoRoot: string,
  snap: GitSnapshot,
  report: RunReport,
  noPush: boolean,
): CommitPushResult {
  if (!isCommittable(snap)) return { sha: null, pushed: false, reason: "dirty_pre_run" };
  if (report.overallStatus !== "passed") return { sha: null, pushed: false, reason: "run_failed" };
  if (report.fixedJobs.length === 0) return { sha: null, pushed: false, reason: "no_fixed_jobs" };

  const cr = commit(repoRoot, report.fixedJobs);
  if (!cr.ok) return { sha: null, pushed: false, reason: cr.reason as CommitPushReason };

  if (noPush || !isPushable(snap)) {
    return { sha: cr.sha, pushed: false, reason: "ok" };
  }

  const pr = push(repoRoot);
  if (!pr.ok) return { sha: cr.sha, pushed: false, reason: "push_failed" };
  return { sha: cr.sha, pushed: true, reason: "ok" };
}

async function parseJobs(repoRoot: string): Promise<CIJob[] | 2> {
  const platform = detectPlatform(repoRoot);
  try {
    return parseCIConfig(repoRoot, platform);
  } catch (err) {
    if (err instanceof CIParseError) {
      console.error(`Error parsing CI config: ${err.message}`);
      return 2;
    }
    throw err;
  }
}

async function classifyJobs(
  allJobs: CIJob[],
  opts: ResolvedOptions,
  repoRoot: string,
): Promise<Record<string, string> | null> {
  const jobNames = allJobs.map((j) => j.name);
  let classifications = loadCache(repoRoot, jobNames);
  if (!classifications) {
    console.log(`STITCH: classifying jobs with ${opts.agent}...`);
    classifications = await classifyWithLLM(jobNames, opts.agent, repoRoot);
    if (classifications) {
      saveCache(repoRoot, jobNames, classifications);
    }
  }
  return classifications;
}

async function runHeadless(
  opts: ResolvedOptions,
  repoRoot: string,
  driver: AgentDriver,
): Promise<number> {
  const jobsOrCode = await parseJobs(repoRoot);
  if (jobsOrCode === 2) return 2;
  const allJobs = jobsOrCode;

  if (allJobs.length === 0) {
    const msg =
      "No CI configuration found (.gitlab-ci.yml, .github/workflows/, or bitbucket-pipelines.yml)";
    if (opts.output === "json") {
      console.log(JSON.stringify({ agent: opts.agent, jobs: [], reason: msg }));
    } else {
      console.log(msg);
    }
    return 0;
  }

  let classifications: Record<string, string> | null = null;
  const filterCfg = {
    only: opts.jobs
      ? opts.jobs
          .split(",")
          .map((j) => j.trim())
          .filter(Boolean)
      : null,
    exclude: opts.excludeJobs,
  };

  if (!filterCfg.only && opts.classification === "llm") {
    classifications = await classifyJobs(allJobs, opts, repoRoot);
  }

  const jobs = applyFilter(allJobs, filterCfg, classifications);

  if (opts.dryRun) {
    printDryRun(jobs);
    return 0;
  }

  const config: Partial<RunnerConfig> = {
    maxAttempts: opts.maxAttempts,
    failFast: opts.failFast,
  };

  if (opts.output === "json") {
    const snap = snapshot(repoRoot);
    const runner = new Runner(repoRoot, driver, config);
    const report = await runner.run(jobs);
    console.log(JSON.stringify(report.toDict(), null, 2));
    const { sha } = autoCommitPush(repoRoot, snap, report, !opts.push);
    recordRun(report, { repoRoot, agent: opts.agent, snap, commitSha: sha });
    return report.exitCode();
  }

  if (opts.watch) {
    return runWatchMode(repoRoot, driver, jobs, opts);
  }

  return 0;
}

function printDryRun(jobs: CIJob[]): void {
  const runnable = jobs.filter((j) => !j.skipReason);
  const skipped = jobs.filter((j) => j.skipReason);
  console.log(`stitch run: dry-run ${runnable.length} runnable, ${skipped.length} skipped`);
  for (const j of runnable) {
    const cmd = j.script.join(" && ").slice(0, 80);
    console.log(`  \u25b6 [${j.stage}] ${j.name}: ${cmd}`);
  }
  for (const j of skipped) {
    console.log(`  \u23ed [${j.stage}] ${j.name} -- ${j.skipReason}`);
  }
}

function loadConfigSafe(repoRoot: string): StitchConfig | null | 2 {
  try {
    return loadConfig(repoRoot);
  } catch (err) {
    if (err instanceof ConfigError) {
      console.error((err as ConfigError).message);
      return 2;
    }
    throw err;
  }
}

async function parseJobsSafe(repoRoot: string): Promise<CIJob[] | 2> {
  return parseJobs(repoRoot);
}

async function runTuiMode(
  opts: ResolvedOptions,
  repoRoot: string,
  driver: AgentDriver,
  allJobs: CIJob[],
): Promise<number> {
  // ── TUI mode: jobs found, launch UI ──────────────────────
  // Lazy import: @opentui/core uses bun:ffi, only load when TUI is needed
  const { StitchUI } = await import("../core/ui/run-ui.js");
  const ui = new StitchUI(opts.agent, repoRoot);
  await ui.start();

  // Step 2: Classify
  let classifications: Record<string, string> | null = null;
  const filterCfg = {
    only: opts.jobs
      ? opts.jobs
          .split(",")
          .map((j) => j.trim())
          .filter(Boolean)
      : null,
    exclude: opts.excludeJobs,
  };

  if (!filterCfg.only && opts.classification === "llm") {
    const jobNames = allJobs.map((j) => j.name);
    ui.setLoading("Loading job cache...", 2);
    classifications = loadCache(repoRoot, jobNames);
    if (!classifications) {
      ui.setLoading(`Classifying ${jobNames.length} jobs with ${opts.agent}...`, 2);
      classifications = await classifyWithLLM(jobNames, opts.agent, repoRoot);
      if (classifications) {
        saveCache(repoRoot, jobNames, classifications);
      }
    }
  }

  let jobs = applyFilter(allJobs, filterCfg, classifications);

  // Ensure welcome screen is visible for at least 2 seconds total
  const elapsed = Date.now() - ui.startedAt;
  const remaining = Math.max(0, 2000 - elapsed);
  ui.setLoading("Ready.", 3);
  if (remaining > 0) await new Promise((r) => setTimeout(r, remaining));

  const noPush = !opts.push;
  const config: Partial<RunnerConfig> = {
    maxAttempts: opts.maxAttempts,
    failFast: opts.failFast,
  };
  const platform = detectPlatform(repoRoot);

  let lastExitCode = 0;

  const runOnce = async () => {
    const currentSnap = snapshot(repoRoot);
    ui.initJobs(jobs);
    const runner = new Runner(repoRoot, driver, config, undefined, ui.callback);
    const report = await runner.run(jobs);
    const { sha, pushed, reason } = autoCommitPush(repoRoot, currentSnap, report, noPush);
    recordRun(report, { repoRoot, agent: opts.agent, snap: currentSnap, commitSha: sha });
    ui.markDone(report, sha, pushed, reason);
    lastExitCode = report.exitCode();
  };

  try {
    await runOnce();

    // Stay open, wait for user input
    while (true) {
      const action = await ui.waitForRerun();
      if (action === "quit") break;
      // Re-parse CI config in case it changed
      try {
        const freshJobs = applyFilter(
          parseCIConfig(repoRoot, platform),
          filterCfg,
          classifications,
        );
        jobs = freshJobs;
      } catch {
        // Keep old jobs if parse fails
      }
      await runOnce();
    }
  } catch {
    // Ctrl+C or error
  }

  ui.stop();
  return lastExitCode;
}

export async function runRunCommand(rawOpts: RunOptions): Promise<number> {
  const repoRoot = resolve(rawOpts.repo);
  if (!existsSync(repoRoot)) {
    console.error(`Error: repo path not found: ${repoRoot}`);
    return 2;
  }

  if (!rawOpts.dryRun && rawOpts.output !== "json") {
    const preSnap = snapshot(repoRoot);
    if (preSnap.branch !== null && !preSnap.clean) {
      console.error(
        "stitch: uncommitted changes present in working tree; auto-commit will be skipped for this run",
      );
    }
  }

  const fileConfigOrCode = loadConfigSafe(repoRoot);
  if (fileConfigOrCode === 2) return 2;
  const fileConfig = fileConfigOrCode;

  // agent: CLI positional takes precedence, then config, then no further default
  const agent = rawOpts.agent || fileConfig?.agent || "claude";
  const opts = resolveOptions({ ...rawOpts, agent }, fileConfig);

  if (!VALID_AGENTS.includes(opts.agent)) {
    console.error(`Unknown agent: ${opts.agent}. Valid: ${VALID_AGENTS.join(", ")}`);
    return 2;
  }

  const driver = buildDriver(opts.agent);
  if (!driver) {
    console.error(`Unknown agent: ${opts.agent}`);
    return 2;
  }

  // For non-TUI modes (dry-run, json, watch), parse inline
  if (opts.dryRun || opts.output === "json" || opts.watch) {
    return runHeadless(opts, repoRoot, driver);
  }

  // ── Pre-TUI checks (before loading OpenTUI) ──────────────
  const allJobsOrCode = await parseJobsSafe(repoRoot);
  if (allJobsOrCode === 2) return 2;
  const allJobs = allJobsOrCode;

  if (allJobs.length === 0) {
    console.log(
      "No CI configuration found (.gitlab-ci.yml, .github/workflows/, or bitbucket-pipelines.yml)",
    );
    return 0;
  }

  return runTuiMode(opts, repoRoot, driver, allJobs);
}

async function runWatchMode(
  repoRoot: string,
  driver: AgentDriver,
  jobs: CIJob[],
  opts: ResolvedOptions,
): Promise<number> {
  const runnable = jobs.filter((j) => !j.skipReason);
  if (runnable.length === 0) {
    console.error("Stitch watch: nothing to run -- all jobs are skipped");
    return 0;
  }

  const config: Partial<RunnerConfig> = { maxAttempts: opts.maxAttempts, failFast: opts.failFast };
  const { StitchUI } = await import("../core/ui/run-ui.js");
  const ui = new StitchUI(opts.agent, repoRoot);
  const watchCfg: Partial<WatchConfig> = { debounceSeconds: opts.debounce };

  const lock = new StitchLock(repoRoot);
  try {
    lock.acquire();
  } catch (err) {
    if (err instanceof LockAcquireError) {
      console.error(`Stitch watch: ${err.message}`);
      return 2;
    }
    throw err;
  }

  await ui.start();

  try {
    const runOnce = async () => {
      const preSnap = snapshot(repoRoot);
      ui.initJobs(jobs);
      const runController = new AbortController();
      ui.setOnAbort(() => runController.abort());
      try {
        const runner = new Runner(repoRoot, driver, config, undefined, ui.callback);
        const report = await runner.run(jobs, false, runController.signal);
        const { sha, pushed, reason } = autoCommitPush(repoRoot, preSnap, report, !opts.push);
        recordRun(report, { repoRoot, agent: opts.agent, snap: preSnap, commitSha: sha });
        ui.markDone(report, sha, pushed, reason);
      } finally {
        ui.setOnAbort(null);
      }
    };

    await runOnce();

    while (true) {
      const abort = new AbortController();
      const fsPromise = waitForChangeThenIdle(repoRoot, watchCfg, abort.signal)
        .then(() => "fs" as const)
        .catch((err) => {
          if (err instanceof AbortedError) return "aborted" as const;
          throw err;
        });
      const keyPromise = ui.waitForRerun(abort.signal);

      let trigger: "fs" | "rerun" | "quit" | "aborted";
      try {
        trigger = await Promise.race([fsPromise, keyPromise]);
      } catch {
        abort.abort();
        break;
      }

      abort.abort();
      if (trigger === "quit") break;
      await runOnce();
    }
  } catch {
    // Ctrl+C
  }

  ui.stop();
  lock.release();

  return 0;
}
