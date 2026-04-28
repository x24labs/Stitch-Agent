import type { CIJob, ExecResult, FixContext, FixOutcome } from "../src/core/models.js";
import type { AgentDriver } from "../src/drivers/types.js";

export class StubExecutor {
  results = new Map<string, ExecResult[]>();
  calls = new Map<string, number>();
  delaysMs = new Map<string, number>();

  async runJob(job: CIJob, signal?: AbortSignal): Promise<ExecResult> {
    const count = (this.calls.get(job.name) ?? 0) + 1;
    this.calls.set(job.name, count);

    const delay = this.delaysMs.get(job.name) ?? 0;
    if (delay > 0) {
      const cancelled = await new Promise<boolean>((resolve) => {
        if (signal?.aborted) return resolve(true);
        const timer = setTimeout(() => {
          signal?.removeEventListener("abort", onAbort);
          resolve(false);
        }, delay);
        const onAbort = () => {
          clearTimeout(timer);
          resolve(true);
        };
        signal?.addEventListener("abort", onAbort, { once: true });
      });
      if (cancelled) {
        return { log: "", exitCode: -1, timedOut: false, cancelled: true, durationSeconds: 0 };
      }
    }

    const queue = this.results.get(job.name) ?? [];
    return queue.shift() ?? { log: "", exitCode: 0, timedOut: false, durationSeconds: 0 };
  }
}

export class StubDriver implements AgentDriver {
  name = "stub";
  onOutput: ((log: string) => void) | null = null;
  outcomes: FixOutcome[] = [];
  calls: FixContext[] = [];

  fixDelayMs = 0;

  async fix(context: FixContext, signal?: AbortSignal): Promise<FixOutcome> {
    this.calls.push(context);
    if (this.fixDelayMs > 0) {
      const cancelled = await new Promise<boolean>((resolve) => {
        if (signal?.aborted) return resolve(true);
        const timer = setTimeout(() => {
          signal?.removeEventListener("abort", onAbort);
          resolve(false);
        }, this.fixDelayMs);
        const onAbort = () => {
          clearTimeout(timer);
          resolve(true);
        };
        signal?.addEventListener("abort", onAbort, { once: true });
      });
      if (cancelled) return { applied: false, reason: "stub aborted", driverLog: "" };
    }
    return this.outcomes.shift() ?? { applied: true, reason: "stub fix applied", driverLog: "" };
  }
}

export function job(name: string, overrides: Partial<CIJob> = {}): CIJob {
  return {
    name,
    stage: "test",
    script: ["echo hi"],
    image: null,
    sourceFile: "",
    skipReason: null,
    ...overrides,
  };
}

export function execResult(overrides: Partial<ExecResult> = {}): ExecResult {
  return { log: "", exitCode: 0, timedOut: false, durationSeconds: 0, ...overrides };
}
