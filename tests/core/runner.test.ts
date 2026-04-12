import { describe, expect, it } from "vitest";
import { Runner } from "../../src/core/runner.js";
import { StubDriver, StubExecutor, execResult, job } from "../helpers.js";

function runner(
  driver: StubDriver,
  executor: StubExecutor,
  opts: { maxAttempts?: number; failFast?: boolean } = {},
) {
  return new Runner(
    "/tmp/repo",
    driver,
    { maxAttempts: opts.maxAttempts ?? 3, failFast: opts.failFast ?? false },
    executor as any,
  );
}

describe("Runner", () => {
  it("job passes first try", async () => {
    const exec = new StubExecutor();
    exec.results.set("lint", [execResult()]);
    const drv = new StubDriver();
    const r = runner(drv, exec);
    const report = await r.run([job("lint")]);
    expect(report.jobs[0]!.status).toBe("passed");
    expect(report.jobs[0]!.attempts).toBe(1);
    expect(drv.calls).toHaveLength(0);
  });

  it("job passes after one fix", async () => {
    const exec = new StubExecutor();
    exec.results.set("lint", [
      execResult({ log: "fail", exitCode: 1 }),
      execResult(),
    ]);
    const drv = new StubDriver();
    const r = runner(drv, exec);
    const report = await r.run([job("lint")]);
    expect(report.jobs[0]!.status).toBe("passed");
    expect(report.jobs[0]!.attempts).toBe(2);
    expect(drv.calls).toHaveLength(1);
  });

  it("job exhausts attempts", async () => {
    const exec = new StubExecutor();
    exec.results.set("lint", [
      execResult({ log: "f1", exitCode: 1 }),
      execResult({ log: "f2", exitCode: 1 }),
      execResult({ log: "f3", exitCode: 1 }),
    ]);
    const drv = new StubDriver();
    const r = runner(drv, exec, { maxAttempts: 3 });
    const report = await r.run([job("lint")]);
    expect(report.jobs[0]!.status).toBe("escalated");
    expect(report.jobs[0]!.attempts).toBe(3);
    expect(drv.calls).toHaveLength(2);
  });

  it("driver refusal escalates immediately", async () => {
    const exec = new StubExecutor();
    exec.results.set("lint", [execResult({ log: "fail", exitCode: 1 })]);
    const drv = new StubDriver();
    drv.outcomes = [{ applied: false, reason: "no can do", driverLog: "" }];
    const r = runner(drv, exec, { maxAttempts: 3 });
    const report = await r.run([job("lint")]);
    expect(report.jobs[0]!.status).toBe("escalated");
    expect(report.jobs[0]!.errorLog).toContain("no can do");
  });

  it("skipped jobs are not executed", async () => {
    const exec = new StubExecutor();
    const drv = new StubDriver();
    const r = runner(drv, exec);
    const skipped = job("deploy", { skipReason: "infra" });
    const report = await r.run([skipped]);
    expect(report.jobs[0]!.status).toBe("skipped");
    expect(report.jobs[0]!.skipReason).toBe("infra");
    expect(exec.calls.size).toBe(0);
  });

  it("dry run marks jobs as not_run", async () => {
    const exec = new StubExecutor();
    const drv = new StubDriver();
    const r = runner(drv, exec);
    const report = await r.run([job("lint"), job("test")], true);
    expect(report.jobs.every((j) => j.status === "not_run")).toBe(true);
    expect(exec.calls.size).toBe(0);
  });

  it("batch fix with two failing jobs", async () => {
    const exec = new StubExecutor();
    exec.results.set("lint", [
      execResult({ log: "lint err", exitCode: 1 }),
      execResult(),
    ]);
    exec.results.set("typecheck", [
      execResult({ log: "type err", exitCode: 1 }),
      execResult(),
    ]);
    const drv = new StubDriver();
    const r = runner(drv, exec);
    const report = await r.run([job("lint"), job("typecheck")]);

    expect(report.jobs[0]!.status).toBe("passed");
    expect(report.jobs[0]!.attempts).toBe(2);
    expect(report.jobs[1]!.status).toBe("passed");
    expect(report.jobs[1]!.attempts).toBe(2);
    // Only ONE driver call for the batch
    expect(drv.calls).toHaveLength(1);
    expect(drv.calls[0]!.jobName).toContain("lint");
    expect(drv.calls[0]!.jobName).toContain("typecheck");
  });

  it("batch fix partial resolution", async () => {
    const exec = new StubExecutor();
    exec.results.set("lint", [
      execResult({ log: "lint err", exitCode: 1 }),
      execResult(),
    ]);
    exec.results.set("typecheck", [
      execResult({ log: "type err", exitCode: 1 }),
      execResult({ log: "type err 2", exitCode: 1 }),
      execResult(),
    ]);
    const drv = new StubDriver();
    const r = runner(drv, exec, { maxAttempts: 3 });
    const report = await r.run([job("lint"), job("typecheck")]);

    expect(report.jobs[0]!.status).toBe("passed");
    expect(report.jobs[0]!.attempts).toBe(2);
    expect(report.jobs[1]!.status).toBe("passed");
    expect(report.jobs[1]!.attempts).toBe(3);
    expect(drv.calls).toHaveLength(2);
    // Second call should be single job (typecheck only)
    expect(drv.calls[1]!.jobName).toBe("typecheck");
    expect(drv.calls[1]!.promptOverride).toBeNull();
  });

  it("driver refusal escalates batch", async () => {
    const exec = new StubExecutor();
    exec.results.set("lint", [execResult({ log: "fail", exitCode: 1 })]);
    exec.results.set("typecheck", [execResult({ log: "fail", exitCode: 1 })]);
    const drv = new StubDriver();
    drv.outcomes = [{ applied: false, reason: "cannot fix", driverLog: "" }];
    const r = runner(drv, exec, { maxAttempts: 3 });
    const report = await r.run([job("lint"), job("typecheck")]);

    expect(report.jobs[0]!.status).toBe("escalated");
    expect(report.jobs[1]!.status).toBe("escalated");
    expect(report.jobs[0]!.errorLog).toContain("cannot fix");
  });

  it("mixed pass/fail parallel", async () => {
    const exec = new StubExecutor();
    exec.results.set("lint", [execResult()]);
    exec.results.set("typecheck", [
      execResult({ log: "type err", exitCode: 1 }),
      execResult(),
    ]);
    const drv = new StubDriver();
    const r = runner(drv, exec);
    const report = await r.run([job("lint"), job("typecheck")]);

    expect(report.jobs[0]!.status).toBe("passed");
    expect(report.jobs[0]!.attempts).toBe(1);
    expect(report.jobs[1]!.status).toBe("passed");
    expect(report.jobs[1]!.attempts).toBe(2);
    expect(drv.calls).toHaveLength(1);
    expect(drv.calls[0]!.jobName).toBe("typecheck");
  });

  it("watch mode (max_attempts=1) no fix", async () => {
    const exec = new StubExecutor();
    exec.results.set("lint", [execResult({ log: "fail", exitCode: 1 })]);
    exec.results.set("test", [execResult()]);
    const drv = new StubDriver();
    const r = runner(drv, exec, { maxAttempts: 1 });
    const report = await r.run([job("lint"), job("test")]);

    expect(report.jobs[0]!.status).toBe("escalated");
    expect(report.jobs[1]!.status).toBe("passed");
    expect(drv.calls).toHaveLength(0);
  });

  it("preserves original job order", async () => {
    const exec = new StubExecutor();
    exec.results.set("lint", [execResult()]);
    exec.results.set("test", [execResult()]);
    const drv = new StubDriver();
    const r = runner(drv, exec);
    const skipped = job("deploy", { skipReason: "infra" });
    const report = await r.run([skipped, job("lint"), job("test")]);

    expect(report.jobs[0]!.name).toBe("deploy");
    expect(report.jobs[0]!.status).toBe("skipped");
    expect(report.jobs[1]!.name).toBe("lint");
    expect(report.jobs[1]!.status).toBe("passed");
    expect(report.jobs[2]!.name).toBe("test");
    expect(report.jobs[2]!.status).toBe("passed");
  });

  it("continues after escalation (default, no fail-fast)", async () => {
    const exec = new StubExecutor();
    exec.results.set("lint", [
      execResult({ exitCode: 1 }),
      execResult({ exitCode: 1 }),
      execResult({ exitCode: 1 }),
    ]);
    exec.results.set("test", [execResult()]);
    const drv = new StubDriver();
    const r = runner(drv, exec, { maxAttempts: 3 });
    const report = await r.run([job("lint"), job("test")]);

    // test passes on first attempt, lint escalates
    expect(report.jobs[0]!.status).toBe("escalated");
    expect(report.jobs[1]!.status).toBe("passed");
  });
});
