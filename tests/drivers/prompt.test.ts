import { describe, expect, it } from "vitest";
import type { FixContext } from "../../src/core/models.js";
import { buildBatchPrompt, buildPrompt } from "../../src/drivers/prompt.js";

function ctx(overrides: Partial<FixContext> = {}): FixContext {
  return {
    repoRoot: "/repo",
    jobName: "lint",
    command: "ruff check .",
    script: ["ruff check ."],
    errorLog: "error: line too long",
    attempt: 1,
    promptOverride: null,
    ...overrides,
  };
}

describe("buildPrompt", () => {
  it("includes job name, command, attempt, and error log", () => {
    const prompt = buildPrompt(ctx());
    expect(prompt).toContain("lint");
    expect(prompt).toContain("ruff check .");
    expect(prompt).toContain("Attempt: 1");
    expect(prompt).toContain("error: line too long");
    expect(prompt).toContain("Working directory: /repo");
  });

  it("returns promptOverride verbatim when set", () => {
    const prompt = buildPrompt(ctx({ promptOverride: "custom prompt" }));
    expect(prompt).toBe("custom prompt");
  });

  it("truncates error log to 12000 chars", () => {
    const longLog = "x".repeat(20_000);
    const prompt = buildPrompt(ctx({ errorLog: longLog }));
    // The log should be truncated from the start, keeping the tail
    expect(prompt).not.toContain("x".repeat(20_000));
    expect(prompt.length).toBeLessThan(20_000);
  });
});

describe("buildBatchPrompt", () => {
  it("includes all job names and error logs", () => {
    const contexts = [
      ctx({ jobName: "lint", errorLog: "lint error" }),
      ctx({ jobName: "test", errorLog: "test error", command: "pytest" }),
    ];
    const prompt = buildBatchPrompt(contexts);
    expect(prompt).toContain("### 1. lint");
    expect(prompt).toContain("### 2. test");
    expect(prompt).toContain("lint error");
    expect(prompt).toContain("test error");
    expect(prompt).toContain("common root cause");
  });

  it("splits log budget across jobs", () => {
    const longLog = "x".repeat(20_000);
    const contexts = [
      ctx({ jobName: "a", errorLog: longLog }),
      ctx({ jobName: "b", errorLog: longLog }),
    ];
    const prompt = buildBatchPrompt(contexts);
    // Each job gets 6000 chars (12000 / 2)
    expect(prompt.length).toBeLessThan(20_000);
  });
});
