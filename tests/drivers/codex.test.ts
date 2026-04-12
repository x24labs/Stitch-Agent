import { describe, expect, it } from "vitest";
import { CodexDriver } from "../../src/drivers/codex.js";
import type { FixContext } from "../../src/core/models.js";

function ctx(overrides: Partial<FixContext> = {}): FixContext {
  return {
    repoRoot: "/tmp",
    jobName: "lint",
    command: "ruff check .",
    script: ["ruff check ."],
    errorLog: "error",
    attempt: 1,
    promptOverride: null,
    ...overrides,
  };
}

describe("CodexDriver", () => {
  it("returns not found when binary missing", async () => {
    const driver = new CodexDriver("nonexistent-binary-xyz");
    const result = await driver.fix(ctx());
    expect(result.applied).toBe(false);
    expect(result.reason).toContain("not found in PATH");
  });

  it("has correct name", () => {
    const driver = new CodexDriver();
    expect(driver.name).toBe("codex");
  });

  it("initializes with default timeout", () => {
    const driver = new CodexDriver();
    expect(driver.timeoutSeconds).toBe(600);
  });
});
