import type { CIJob, ExecResult, FixContext, FixOutcome } from "../src/core/models.js";
import type { AgentDriver } from "../src/drivers/types.js";

export class StubExecutor {
  results = new Map<string, ExecResult[]>();
  calls = new Map<string, number>();

  async runJob(job: CIJob): Promise<ExecResult> {
    const count = (this.calls.get(job.name) ?? 0) + 1;
    this.calls.set(job.name, count);
    const queue = this.results.get(job.name) ?? [];
    return (
      queue.shift() ?? { log: "", exitCode: 0, timedOut: false, durationSeconds: 0 }
    );
  }
}

export class StubDriver implements AgentDriver {
  name = "stub";
  onOutput: ((log: string) => void) | null = null;
  outcomes: FixOutcome[] = [];
  calls: FixContext[] = [];

  async fix(context: FixContext): Promise<FixOutcome> {
    this.calls.push(context);
    return (
      this.outcomes.shift() ?? { applied: true, reason: "stub fix applied", driverLog: "" }
    );
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
