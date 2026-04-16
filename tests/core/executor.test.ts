import { describe, expect, it } from "vitest";
import { LocalExecutor } from "../../src/core/executor.js";
import type { CIJob } from "../../src/core/models.js";

function ciJob(script: string[]): CIJob {
  return { name: "test", stage: "test", script, image: null, sourceFile: "", skipReason: null };
}

describe("LocalExecutor", () => {
  it("executes all commands and returns exit 0", async () => {
    const exec = new LocalExecutor("/tmp", 30);
    const result = await exec.runJob(ciJob(["echo hello", "echo world"]));
    expect(result.exitCode).toBe(0);
    expect(result.log).toContain("hello");
    expect(result.log).toContain("world");
  });

  it("stops on first failing command", async () => {
    const exec = new LocalExecutor("/tmp", 30);
    const result = await exec.runJob(ciJob(["echo before", "exit 1", "echo after"]));
    expect(result.exitCode).toBe(1);
    expect(result.log).toContain("before");
    expect(result.log).not.toContain("after");
  });

  it("returns exit 0 for empty script", async () => {
    const exec = new LocalExecutor("/tmp", 30);
    const result = await exec.runJob(ciJob([]));
    expect(result.exitCode).toBe(0);
  });

  it("sets STITCH_RUN env var", async () => {
    const exec = new LocalExecutor("/tmp", 30);
    const result = await exec.runJob(ciJob(["echo $STITCH_RUN"]));
    expect(result.exitCode).toBe(0);
    expect(result.log).toContain("1");
  });

  it("tracks duration", async () => {
    const exec = new LocalExecutor("/tmp", 30);
    const result = await exec.runJob(ciJob(["echo fast"]));
    expect(result.durationSeconds).toBeGreaterThanOrEqual(0);
    expect(result.timedOut).toBe(false);
  });

  it("handles timeout", async () => {
    const exec = new LocalExecutor("/tmp", 2);
    const result = await exec.runJob(ciJob(["sleep 60"]));
    expect(result.timedOut).toBe(true);
    expect(result.exitCode).toBe(124);
  }, 30000);

  it("prepends sudo for apt-get when not root", async () => {
    // This test just verifies the command string is modified in the log
    const exec = new LocalExecutor("/tmp", 5);
    // We can't actually run apt-get, so we just check the log shows sudo was prepended
    const result = await exec.runJob(ciJob(["apt-get update"]));
    if (process.getuid?.() !== 0) {
      expect(result.log).toContain("sudo apt-get");
    }
  });
});
