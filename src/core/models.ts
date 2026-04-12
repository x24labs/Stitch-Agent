import { z } from "zod";

export type JobStatus = "passed" | "escalated" | "skipped" | "not_run" | "failed";

export interface CIJob {
  name: string;
  stage: string;
  script: string[];
  image: string | null;
  sourceFile: string;
  skipReason: string | null;
}

export interface FixContext {
  repoRoot: string;
  jobName: string;
  command: string;
  script: string[];
  errorLog: string;
  attempt: number;
  promptOverride: string | null;
}

export interface FixOutcome {
  applied: boolean;
  reason: string;
  driverLog: string;
}

export interface JobResult {
  name: string;
  status: JobStatus;
  attempts: number;
  driver: string | null;
  errorLog: string;
  skipReason: string | null;
}

export interface ExecResult {
  log: string;
  exitCode: number;
  timedOut: boolean;
  durationSeconds: number;
}

export interface GitSnapshot {
  clean: boolean;
  branch: string | null;
  hasRemote: boolean;
  ahead: number;
}

export interface CommitResult {
  ok: boolean;
  sha: string;
  message: string;
}

export interface PushResult {
  ok: boolean;
  error: string;
}

export function isCommittable(snap: GitSnapshot): boolean {
  return snap.clean && snap.branch !== null;
}

export function isPushable(snap: GitSnapshot): boolean {
  return isCommittable(snap) && !(snap.hasRemote && snap.ahead > 0);
}

export class RunReport {
  jobs: JobResult[];
  agent: string;

  constructor(jobs: JobResult[] = [], agent = "") {
    this.jobs = jobs;
    this.agent = agent;
  }

  get fixedJobs(): string[] {
    return this.jobs
      .filter((j) => j.status === "passed" && j.attempts > 1)
      .map((j) => j.name);
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

// Zod schemas for external data validation

export const CIJobSchema = z.object({
  name: z.string(),
  stage: z.string(),
  script: z.array(z.string()),
  image: z.string().nullable(),
  sourceFile: z.string(),
  skipReason: z.string().nullable(),
});

export const ClassificationCacheSchema = z.object({
  hash: z.string(),
  jobs: z.record(z.enum(["verify", "infra"])),
});
