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
    executor,
  );
}

describe("Runner", () => {
  it("job passes first try", async () => {
    const exec = new StubExecutor();
    exec.results.set("lint", [execResult()]);
    const drv = new StubDriver();
    const r = runner(drv, exec);
    const report = await r.run([job("lint")]);
    expect(report.jobs[0]?.status).toBe("passed");
    expect(report.jobs[0]?.attempts).toBe(1);
    expect(drv.calls).toHaveLength(0);
  });

  it("job passes after one fix", async () => {
    const exec = new StubExecutor();
    exec.results.set("lint", [execResult({ log: "fail", exitCode: 1 }), execResult()]);
    const drv = new StubDriver();
    const r = runner(drv, exec);
    const report = await r.run([job("lint")]);
    expect(report.jobs[0]?.status).toBe("passed");
    expect(report.jobs[0]?.attempts).toBe(2);
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
    expect(report.jobs[0]?.status).toBe("escalated");
    expect(report.jobs[0]?.attempts).toBe(3);
    expect(drv.calls).toHaveLength(2);
  });

  it("driver refusal escalates immediately", async () => {
    const exec = new StubExecutor();
    exec.results.set("lint", [execResult({ log: "fail", exitCode: 1 })]);
    const drv = new StubDriver();
    drv.outcomes = [{ applied: false, reason: "no can do", driverLog: "" }];
    const r = runner(drv, exec, { maxAttempts: 3 });
    const report = await r.run([job("lint")]);
    expect(report.jobs[0]?.status).toBe("escalated");
    expect(report.jobs[0]?.errorLog).toContain("no can do");
  });

  it("skipped jobs are not executed", async () => {
    const exec = new StubExecutor();
    const drv = new StubDriver();
    const r = runner(drv, exec);
    const skipped = job("deploy", { skipReason: "infra" });
    const report = await r.run([skipped]);
    expect(report.jobs[0]?.status).toBe("skipped");
    expect(report.jobs[0]?.skipReason).toBe("infra");
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
    exec.results.set("lint", [execResult({ log: "lint err", exitCode: 1 }), execResult()]);
    exec.results.set("typecheck", [execResult({ log: "type err", exitCode: 1 }), execResult()]);
    const drv = new StubDriver();
    const r = runner(drv, exec);
    const report = await r.run([job("lint"), job("typecheck")]);

    expect(report.jobs[0]?.status).toBe("passed");
    expect(report.jobs[0]?.attempts).toBe(2);
    expect(report.jobs[1]?.status).toBe("passed");
    expect(report.jobs[1]?.attempts).toBe(2);
    // Only ONE driver call for the batch
    expect(drv.calls).toHaveLength(1);
    expect(drv.calls[0]?.jobName).toContain("lint");
    expect(drv.calls[0]?.jobName).toContain("typecheck");
  });

  it("batch fix partial resolution", async () => {
    const exec = new StubExecutor();
    exec.results.set("lint", [execResult({ log: "lint err", exitCode: 1 }), execResult()]);
    exec.results.set("typecheck", [
      execResult({ log: "type err", exitCode: 1 }),
      execResult({ log: "type err 2", exitCode: 1 }),
      execResult(),
    ]);
    const drv = new StubDriver();
    const r = runner(drv, exec, { maxAttempts: 3 });
    const report = await r.run([job("lint"), job("typecheck")]);

    expect(report.jobs[0]?.status).toBe("passed");
    expect(report.jobs[0]?.attempts).toBe(2);
    expect(report.jobs[1]?.status).toBe("passed");
    expect(report.jobs[1]?.attempts).toBe(3);
    expect(drv.calls).toHaveLength(2);
    // Second call should be single job (typecheck only)
    expect(drv.calls[1]?.jobName).toBe("typecheck");
    expect(drv.calls[1]?.promptOverride).toBeNull();
  });

  it("driver refusal escalates batch", async () => {
    const exec = new StubExecutor();
    exec.results.set("lint", [execResult({ log: "fail", exitCode: 1 })]);
    exec.results.set("typecheck", [execResult({ log: "fail", exitCode: 1 })]);
    const drv = new StubDriver();
    drv.outcomes = [{ applied: false, reason: "cannot fix", driverLog: "" }];
    const r = runner(drv, exec, { maxAttempts: 3 });
    const report = await r.run([job("lint"), job("typecheck")]);

    expect(report.jobs[0]?.status).toBe("escalated");
    expect(report.jobs[1]?.status).toBe("escalated");
    expect(report.jobs[0]?.errorLog).toContain("cannot fix");
  });

  it("mixed pass/fail parallel", async () => {
    const exec = new StubExecutor();
    exec.results.set("lint", [execResult()]);
    exec.results.set("typecheck", [execResult({ log: "type err", exitCode: 1 }), execResult()]);
    const drv = new StubDriver();
    const r = runner(drv, exec);
    const report = await r.run([job("lint"), job("typecheck")]);

    expect(report.jobs[0]?.status).toBe("passed");
    expect(report.jobs[0]?.attempts).toBe(1);
    expect(report.jobs[1]?.status).toBe("passed");
    expect(report.jobs[1]?.attempts).toBe(2);
    expect(drv.calls).toHaveLength(1);
    expect(drv.calls[0]?.jobName).toBe("typecheck");
  });

  it("watch mode (max_attempts=1) no fix", async () => {
    const exec = new StubExecutor();
    exec.results.set("lint", [execResult({ log: "fail", exitCode: 1 })]);
    exec.results.set("test", [execResult()]);
    const drv = new StubDriver();
    const r = runner(drv, exec, { maxAttempts: 1 });
    const report = await r.run([job("lint"), job("test")]);

    expect(report.jobs[0]?.status).toBe("escalated");
    expect(report.jobs[1]?.status).toBe("passed");
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

    expect(report.jobs[0]?.name).toBe("deploy");
    expect(report.jobs[0]?.status).toBe("skipped");
    expect(report.jobs[1]?.name).toBe("lint");
    expect(report.jobs[1]?.status).toBe("passed");
    expect(report.jobs[2]?.name).toBe("test");
    expect(report.jobs[2]?.status).toBe("passed");
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
    expect(report.jobs[0]?.status).toBe("escalated");
    expect(report.jobs[1]?.status).toBe("passed");
  });

  describe("fail-fast", () => {
    it("cancels in-flight jobs when one fails, then fixes and re-verifies", async () => {
      const exec = new StubExecutor();
      // lint fails fast; test is slow and will be cancelled
      exec.results.set("lint", [execResult({ log: "lint err", exitCode: 1 }), execResult()]);
      exec.results.set("test", [execResult(), execResult()]);
      exec.delaysMs.set("test", 100);
      const drv = new StubDriver();
      const r = runner(drv, exec, { maxAttempts: 3, failFast: true });
      const report = await r.run([job("lint"), job("test")]);

      expect(report.jobs[0]?.status).toBe("passed");
      expect(report.jobs[1]?.status).toBe("passed");
      // lint ran twice (fail + fixed), test ran twice (cancelled on attempt 1, passed on attempt 2)
      expect(exec.calls.get("lint")).toBe(2);
      expect(exec.calls.get("test")).toBe(2);
      // Batch fix received only the known failure (lint), not the cancelled test
      expect(drv.calls).toHaveLength(1);
      expect(drv.calls[0]?.jobName).toBe("lint");
    });

    it("disables itself on the final attempt so cancelled jobs cannot hide", async () => {
      // If fail-fast kept firing on the last attempt, cancelled jobs would be left
      // in limbo (not passed, not failed). On the final attempt we always run the
      // full set so every job gets a verdict.
      const exec = new StubExecutor();
      // lint keeps failing; test is slow and would be cancelled if fail-fast stayed on
      exec.results.set("lint", [
        execResult({ exitCode: 1 }),
        execResult({ exitCode: 1 }),
        execResult({ exitCode: 1 }),
      ]);
      exec.results.set("test", [execResult(), execResult(), execResult()]);
      exec.delaysMs.set("test", 50);
      const drv = new StubDriver();
      const r = runner(drv, exec, { maxAttempts: 2, failFast: true });
      const report = await r.run([job("lint"), job("test")]);

      expect(report.jobs[0]?.status).toBe("escalated");
      // test must have a verdict by the end, not stay cancelled
      expect(report.jobs[1]?.status).toBe("passed");
    });

    it("has no effect when no jobs fail", async () => {
      const exec = new StubExecutor();
      exec.results.set("lint", [execResult()]);
      exec.results.set("test", [execResult()]);
      const drv = new StubDriver();
      const r = runner(drv, exec, { failFast: true });
      const report = await r.run([job("lint"), job("test")]);

      expect(report.jobs.every((j) => j.status === "passed")).toBe(true);
      expect(exec.calls.get("lint")).toBe(1);
      expect(exec.calls.get("test")).toBe(1);
    });
  });

  describe("external abort signal", () => {
    it("aborts in-flight jobs and marks them not_run", async () => {
      const exec = new StubExecutor();
      exec.results.set("lint", [execResult()]);
      exec.delaysMs.set("lint", 500);
      const drv = new StubDriver();
      const r = runner(drv, exec);
      const controller = new AbortController();
      setTimeout(() => controller.abort(), 20);
      const report = await r.run([job("lint")], false, controller.signal);
      expect(report.jobs[0]?.status).toBe("not_run");
      expect(report.jobs[0]?.skipReason).toBe("aborted");
    });

    it("aborts driver fix and stops the attempt loop", async () => {
      const exec = new StubExecutor();
      exec.results.set("lint", [
        execResult({ exitCode: 1 }),
        execResult({ exitCode: 1 }),
        execResult({ exitCode: 1 }),
      ]);
      const drv = new StubDriver();
      drv.fixDelayMs = 500;
      const r = runner(drv, exec, { maxAttempts: 3 });
      const controller = new AbortController();
      setTimeout(() => controller.abort(), 20);
      const report = await r.run([job("lint")], false, controller.signal);
      expect(report.jobs[0]?.status).toBe("not_run");
      expect(drv.calls).toHaveLength(1);
    });

    it("no abort = normal path", async () => {
      const exec = new StubExecutor();
      exec.results.set("lint", [execResult()]);
      const drv = new StubDriver();
      const r = runner(drv, exec);
      const controller = new AbortController();
      const report = await r.run([job("lint")], false, controller.signal);
      expect(report.jobs[0]?.status).toBe("passed");
    });
  });
});
