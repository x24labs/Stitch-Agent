/** Outcome of a single job execution. */
export type JobStatus = "passed" | "escalated" | "skipped" | "not_run" | "failed";

/** A CI job parsed from `.gitlab-ci.yml` or similar config. */
export interface CIJob {
  name: string;
  stage: string;
  script: string[];
  image: string | null;
  sourceFile: string;
  skipReason: string | null;
}

/** Input passed to an agent driver when requesting a fix. */
export interface FixContext {
  repoRoot: string;
  jobName: string;
  command: string;
  script: string[];
  errorLog: string;
  attempt: number;
  promptOverride: string | null;
}

/** Result returned by an agent driver after a fix attempt. */
export interface FixOutcome {
  applied: boolean;
  reason: string;
  driverLog: string;
}

/** Final result of running a single job through the fix loop. */
export interface JobResult {
  name: string;
  status: JobStatus;
  attempts: number;
  driver: string | null;
  errorLog: string;
  skipReason: string | null;
}

/** Output from executing a shell command locally. */
export interface ExecResult {
  log: string;
  exitCode: number;
  timedOut: boolean;
  durationSeconds: number;
  cancelled?: boolean;
}

/** Point-in-time snapshot of the repository's git state. */
export interface GitSnapshot {
  clean: boolean;
  branch: string | null;
  hasRemote: boolean;
  ahead: number;
}

/** Outcome of a `git commit` operation. */
export interface CommitResult {
  ok: boolean;
  sha: string;
  message: string;
}

/** Outcome of a `git push` operation. */
export interface PushResult {
  ok: boolean;
  error: string;
}

/** True when the working tree is clean and on a named branch. */
export function isCommittable(snap: GitSnapshot): boolean {
  return snap.clean && snap.branch !== null;
}

/** True when committable and not ahead of the remote tracking branch. */
export function isPushable(snap: GitSnapshot): boolean {
  return isCommittable(snap) && !(snap.hasRemote && snap.ahead > 0);
}

/** Aggregated results from a full `stitch run` invocation. */
export class RunReport {
  jobs: JobResult[];
  agent: string;

  constructor(jobs: JobResult[] = [], agent = "") {
    this.jobs = jobs;
    this.agent = agent;
  }

  get fixedJobs(): string[] {
    return this.jobs.filter((j) => j.status === "passed" && j.attempts > 1).map((j) => j.name);
  }

  get overallStatus(): "passed" | "failed" {
    const nonSkipped = this.jobs.filter((j) => j.status !== "skipped");
    if (nonSkipped.length === 0) return "passed";
    return nonSkipped.every((j) => j.status === "passed") ? "passed" : "failed";
  }

  exitCode(): number {
    return this.overallStatus === "passed" ? 0 : 1;
  }

  toDict(): Record<string, unknown> {
    return {
      agent: this.agent,
      overall_status: this.overallStatus,
      jobs: this.jobs.map((j) => ({
        name: j.name,
        status: j.status,
        attempts: j.attempts,
        driver: j.driver,
        skip_reason: j.skipReason,
        error_log: j.errorLog,
      })),
    };
  }
}
