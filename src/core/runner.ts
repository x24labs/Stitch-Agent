import { buildBatchPrompt } from "../drivers/prompt.js";
import type { AgentDriver } from "../drivers/types.js";
import { LocalExecutor } from "./executor.js";
import type { CIJob, ExecResult, FixContext, JobResult } from "./models.js";
import { RunReport } from "./models.js";

export interface JobExecutor {
  runJob(job: CIJob, signal?: AbortSignal): Promise<ExecResult>;
}

const ERROR_LOG_TAIL_CHARS = 4_000;

export interface RunnerCallback {
  jobStarted(name: string, attempt: number, maxAttempts: number): void;
  jobLogUpdate(name: string, log: string): void;
  jobFinished(name: string, result: JobResult): void;
  driverStarted(name: string, driverName: string): void;
  driverLogUpdate(name: string, log: string): void;
}

class NullCallback implements RunnerCallback {
  jobStarted(_name: string, _attempt: number, _maxAttempts: number): void {}
  jobLogUpdate(_name: string, _log: string): void {}
  jobFinished(_name: string, _result: JobResult): void {}
  driverStarted(_name: string, _driverName: string): void {}
  driverLogUpdate(_name: string, _log: string): void {}
}

export interface RunnerConfig {
  maxAttempts: number;
  failFast: boolean;
  jobTimeoutSeconds: number;
}

const DEFAULT_CONFIG: RunnerConfig = {
  maxAttempts: 3,
  failFast: false,
  jobTimeoutSeconds: 300,
};

function notRunResult(name: string): JobResult {
  return {
    name,
    status: "not_run",
    attempts: 0,
    driver: null,
    errorLog: "",
    skipReason: null,
  };
}

export class Runner {
  private repoRoot: string;
  private driver: AgentDriver;
  private config: RunnerConfig;
  private executor: JobExecutor;
  private cb: RunnerCallback;

  constructor(
    repoRoot: string,
    driver: AgentDriver,
    config?: Partial<RunnerConfig>,
    executor?: JobExecutor,
    callback?: RunnerCallback,
  ) {
    this.repoRoot = repoRoot;
    this.driver = driver;
    this.config = { ...DEFAULT_CONFIG, ...config };
    this.executor = executor ?? new LocalExecutor(repoRoot, this.config.jobTimeoutSeconds);
    this.cb = callback ?? new NullCallback();
  }

  async run(jobs: CIJob[], dryRun = false): Promise<RunReport> {
    const results = new Map<string, JobResult>();
    const runnable = this.partitionJobs(jobs, dryRun, results);

    if (runnable.length === 0) {
      return new RunReport(
        jobs.map((j) => results.get(j.name) ?? notRunResult(j.name)),
        this.driver.name,
      );
    }

    let pending = [...runnable];

    for (let attempt = 1; attempt <= this.config.maxAttempts; attempt++) {
      // On the final attempt, disable fail-fast so we get a complete picture
      // before escalating. Cancelled jobs cannot escalate because we never
      // learned whether they would have passed or failed.
      const useFailFast = this.config.failFast && attempt < this.config.maxAttempts;
      const execResults = await this.runJobsParallel(pending, attempt, useFailFast);

      const { failed, cancelled } = this.classifyResults(pending, execResults, attempt, results);

      if (failed.length === 0 && cancelled.length === 0) break;
      if (failed.length === 0) {
        // Only cancelled jobs remain (shouldn't happen past attempt 1 because
        // we disable fail-fast on the final attempt, but be defensive).
        pending = cancelled;
        continue;
      }

      if (attempt >= this.config.maxAttempts) {
        this.escalateJobs(failed, attempt, results);
        break;
      }

      // Batch fix
      const contexts: FixContext[] = failed.map(([job, log]) => ({
        repoRoot: this.repoRoot,
        jobName: job.name,
        command: job.script.join(" && "),
        script: [...job.script],
        errorLog: log,
        attempt,
        promptOverride: null,
      }));

      const batchContext = this.makeBatchContext(contexts);
      const batchLabel = failed.map(([j]) => j.name).join(", ");

      this.cb.driverStarted(batchLabel, this.driver.name);

      this.driver.onOutput = (log: string) => {
        this.cb.driverLogUpdate(batchLabel, log);
      };

      const outcome = await this.driver.fix(batchContext);
      this.driver.onOutput = null;

      if (!outcome.applied) {
        this.escalateJobs(failed, attempt, results, outcome.reason);
        break;
      }

      // Next round: failed jobs + any that got cancelled by fail-fast
      pending = [...failed.map(([job]) => job), ...cancelled];
    }

    // Preserve original order
    const ordered = jobs.map((j) => results.get(j.name) ?? notRunResult(j.name));
    return new RunReport(ordered, this.driver.name);
  }

  private async runJobsParallel(
    jobs: CIJob[],
    attempt: number,
    failFast: boolean,
  ): Promise<Map<string, ExecResult>> {
    for (const job of jobs) {
      this.cb.jobStarted(job.name, attempt, this.config.maxAttempts);
    }

    const controller = failFast ? new AbortController() : undefined;
    const pairs = await Promise.all(
      jobs.map(async (job): Promise<[string, ExecResult]> => {
        const result = await this.executor.runJob(job, controller?.signal);
        if (!result.cancelled) {
          this.cb.jobLogUpdate(job.name, result.log);
        }
        if (failFast && !result.cancelled && result.exitCode !== 0) {
          controller?.abort();
        }
        return [job.name, result];
      }),
    );

    return new Map(pairs);
  }

  private classifyResults(
    pending: CIJob[],
    execResults: Map<string, ExecResult>,
    attempt: number,
    results: Map<string, JobResult>,
  ): { failed: [CIJob, string][]; cancelled: CIJob[] } {
    const failed: [CIJob, string][] = [];
    const cancelled: CIJob[] = [];
    for (const job of pending) {
      const er = execResults.get(job.name);
      if (!er) continue;
      if (er.cancelled) {
        cancelled.push(job);
        continue;
      }
      if (er.exitCode === 0) {
        const result: JobResult = {
          name: job.name,
          status: "passed",
          attempts: attempt,
          driver: null,
          errorLog: "",
          skipReason: null,
        };
        results.set(job.name, result);
        this.cb.jobFinished(job.name, result);
      } else {
        failed.push([job, er.log]);
      }
    }
    return { failed, cancelled };
  }

  private escalateJobs(
    failed: [CIJob, string][],
    attempt: number,
    results: Map<string, JobResult>,
    driverReason?: string,
  ): void {
    for (const [job, log] of failed) {
      const prefix = driverReason ? `[driver: ${driverReason}]\n\n` : "";
      const result: JobResult = {
        name: job.name,
        status: "escalated",
        attempts: attempt,
        driver: this.driver.name,
        errorLog: `${prefix}${log.slice(-ERROR_LOG_TAIL_CHARS)}`,
        skipReason: null,
      };
      results.set(job.name, result);
      this.cb.jobFinished(job.name, result);
    }
  }

  private partitionJobs(jobs: CIJob[], dryRun: boolean, results: Map<string, JobResult>): CIJob[] {
    const runnable: CIJob[] = [];
    for (const job of jobs) {
      if (job.skipReason) {
        results.set(job.name, {
          name: job.name,
          status: "skipped",
          attempts: 0,
          driver: null,
          errorLog: "",
          skipReason: job.skipReason,
        });
      } else if (dryRun) {
        results.set(job.name, notRunResult(job.name));
      } else {
        runnable.push(job);
      }
    }
    return runnable;
  }

  private makeBatchContext(contexts: FixContext[]): FixContext {
    const [first, ...rest] = contexts;
    if (!first) throw new Error("makeBatchContext called with empty contexts");
    if (rest.length === 0) return first;

    const prompt = buildBatchPrompt(contexts);
    return {
      repoRoot: first.repoRoot,
      jobName: contexts.map((c) => c.jobName).join(", "),
      command: "(batch fix)",
      script: [],
      errorLog: "",
      attempt: first.attempt,
      promptOverride: prompt,
    };
  }
}
