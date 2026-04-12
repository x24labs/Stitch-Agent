import { resolve } from "node:path";
import { existsSync } from "node:fs";
import { detectPlatform } from "../core/ci-detect.js";
import { CIParseError, parseCIConfig } from "../core/ci-parser.js";
import { applyFilter, classifyWithLLM, loadCache, saveCache } from "../core/filter.js";
import { commit, push, snapshot } from "../core/git.js";
import type { CIJob, GitSnapshot } from "../core/models.js";
import { RunReport, isCommittable, isPushable } from "../core/models.js";
import { Runner, type RunnerConfig } from "../core/runner.js";
import { LockAcquireError, StitchLock, WatchConfig, waitForChangeThenIdle } from "../core/watcher.js";
import { StitchUI } from "../core/ui/run-ui.js";
import { ClaudeCodeDriver } from "../drivers/claude-code.js";
import { CodexDriver } from "../drivers/codex.js";
import type { AgentDriver } from "../drivers/types.js";

export interface RunOptions {
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
}

function autoCommitPush(
  repoRoot: string,
  snap: GitSnapshot,
  report: RunReport,
  noPush: boolean,
): CommitPushResult {
  if (!isCommittable(snap) || report.overallStatus !== "passed" || report.fixedJobs.length === 0) {
    return { sha: null, pushed: false };
  }

  const cr = commit(repoRoot, report.fixedJobs);
  if (!cr.ok) return { sha: null, pushed: false };

  let pushed = false;
  if (!noPush && isPushable(snap)) {
    const pr = push(repoRoot);
    pushed = pr.ok;
  }

  return { sha: cr.sha, pushed };
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

export async function runRunCommand(opts: RunOptions): Promise<number> {
  const repoRoot = resolve(opts.repo);
  if (!existsSync(repoRoot)) {
    console.error(`Error: repo path not found: ${repoRoot}`);
    return 2;
  }

  const platform = detectPlatform(repoRoot);

  let allJobs: CIJob[];
  try {
    allJobs = parseCIConfig(repoRoot, platform);
  } catch (err) {
    if (err instanceof CIParseError) {
      console.error(`Error parsing CI config: ${err.message}`);
      return 2;
    }
    throw err;
  }

  if (allJobs.length === 0) {
    const msg = "No CI configuration found (.gitlab-ci.yml or .github/workflows/)";
    if (opts.output === "json") {
      console.log(JSON.stringify({ agent: opts.agent, jobs: [], reason: msg }));
    } else {
      console.log(msg);
    }
    return 0;
  }

  // Filter
  let classifications: Record<string, string> | null = null;
  const filterCfg = {
    only: opts.jobs ? opts.jobs.split(",").map((j) => j.trim()).filter(Boolean) : null,
  };

  if (!filterCfg.only) {
    const jobNames = allJobs.map((j) => j.name);
    classifications = loadCache(repoRoot, jobNames);
    if (!classifications) {
      console.log(`Stitch: classifying jobs with ${opts.agent}...`);
      classifications = await classifyWithLLM(jobNames, opts.agent, repoRoot);
      if (classifications) {
        saveCache(repoRoot, jobNames, classifications);
        console.log("Stitch: saved to .stitch/jobs.json");
      }
    }
  }

  let jobs = applyFilter(allJobs, filterCfg, classifications);

  if (!VALID_AGENTS.includes(opts.agent)) {
    console.error(`Unknown agent: ${opts.agent}. Valid: ${VALID_AGENTS.join(", ")}`);
    return 2;
  }

  if (opts.dryRun) {
    printDryRun(jobs);
    return 0;
  }

  const driver = buildDriver(opts.agent);
  if (!driver) {
    console.error(`Unknown agent: ${opts.agent}`);
    return 2;
  }

  if (opts.watch) {
    return runWatchMode(repoRoot, driver, jobs, opts);
  }

  const snap = snapshot(repoRoot);
  const noPush = !opts.push;

  const config: Partial<RunnerConfig> = {
    maxAttempts: opts.maxAttempts,
    failFast: opts.failFast,
  };

  if (opts.output === "json") {
    const runner = new Runner(repoRoot, driver, config);
    const report = await runner.run(jobs);
    console.log(JSON.stringify(report.toDict(), null, 2));
    autoCommitPush(repoRoot, snap, report, noPush);
    return report.exitCode();
  }

  // Storm TUI mode - persistent, re-runnable
  const ui = new StitchUI(opts.agent, repoRoot);
  ui.start();

  // Welcome screen visible for 2 seconds
  await new Promise((r) => setTimeout(r, 2000));

  let lastExitCode = 0;

  const runOnce = async () => {
    const currentSnap = snapshot(repoRoot);
    ui.initJobs(jobs);
    const runner = new Runner(repoRoot, driver, config, undefined, ui.callback);
    const report = await runner.run(jobs);
    const { sha, pushed } = autoCommitPush(repoRoot, currentSnap, report, noPush);
    ui.markDone(report, sha, pushed);
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

async function runWatchMode(
  repoRoot: string,
  driver: AgentDriver,
  jobs: CIJob[],
  opts: RunOptions,
): Promise<number> {
  const runnable = jobs.filter((j) => !j.skipReason);
  if (runnable.length === 0) {
    console.error("Stitch watch: nothing to run -- all jobs are skipped");
    return 0;
  }

  const config: Partial<RunnerConfig> = { maxAttempts: 1, failFast: false };
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

  ui.start();

  try {
    const runOnce = async () => {
      ui.initJobs(jobs);
      const runner = new Runner(repoRoot, driver, config, undefined, ui.callback);
      const report = await runner.run(jobs);
      ui.markDone(report, null, false);
    };

    await runOnce();

    while (true) {
      try {
        await waitForChangeThenIdle(repoRoot, watchCfg);
      } catch {
        break;
      }
      await runOnce();
    }
  } catch {
    // Ctrl+C
  }

  ui.stop();
  lock.release();

  return 0;
}
