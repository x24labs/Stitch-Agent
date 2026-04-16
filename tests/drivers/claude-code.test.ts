import { describe, expect, it } from "vitest";
import type { FixContext } from "../../src/core/models.js";
import { ClaudeCodeDriver } from "../../src/drivers/claude-code.js";

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

describe("ClaudeCodeDriver", () => {
  it("returns not found when binary missing", async () => {
    const driver = new ClaudeCodeDriver("nonexistent-binary-xyz");
    const result = await driver.fix(ctx());
    expect(result.applied).toBe(false);
    expect(result.reason).toContain("not found in PATH");
  });

  it("has correct name", () => {
    const driver = new ClaudeCodeDriver();
    expect(driver.name).toBe("claude");
  });

  it("initializes with default timeout", () => {
    const driver = new ClaudeCodeDriver();
    expect(driver.timeoutSeconds).toBe(600);
  });

  it("accepts custom timeout", () => {
    const driver = new ClaudeCodeDriver("claude", 120);
    expect(driver.timeoutSeconds).toBe(120);
  });

  it("onOutput is null by default", () => {
    const driver = new ClaudeCodeDriver();
    expect(driver.onOutput).toBeNull();
  });
});
