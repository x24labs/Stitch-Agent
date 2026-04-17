import { describe, expect, it } from "vitest";
import type { FixContext } from "../../src/core/models.js";
import {
  ClaudeCodeDriver,
  handleStdoutLine,
  parseEvent,
} from "../../src/drivers/claude-code.js";

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

describe("parseEvent", () => {
  it("returns null for invalid JSON", () => {
    expect(parseEvent("not json")).toBeNull();
  });

  it("returns null for unknown event type", () => {
    expect(parseEvent(JSON.stringify({ type: "system" }))).toBeNull();
  });

  it("parses assistant text block", () => {
    const line = JSON.stringify({
      type: "assistant",
      message: { content: [{ type: "text", text: "Hello" }] },
    });
    expect(parseEvent(line)).toEqual({ kind: "text", content: "Hello" });
  });

  it("joins multiple text blocks with space", () => {
    const line = JSON.stringify({
      type: "assistant",
      message: {
        content: [
          { type: "text", text: "foo" },
          { type: "text", text: "bar" },
        ],
      },
    });
    expect(parseEvent(line)).toEqual({ kind: "text", content: "foo bar" });
  });

  it("parses assistant tool_use with description", () => {
    const line = JSON.stringify({
      type: "assistant",
      message: {
        content: [{ type: "tool_use", name: "Bash", input: { description: "run lint" } }],
      },
    });
    expect(parseEvent(line)).toEqual({ kind: "tool_use", content: "Bash: run lint" });
  });

  it("falls back to command when no description", () => {
    const line = JSON.stringify({
      type: "assistant",
      message: {
        content: [{ type: "tool_use", name: "Bash", input: { command: "ls -la" } }],
      },
    });
    expect(parseEvent(line)).toEqual({ kind: "tool_use", content: "Bash: ls -la" });
  });

  it("parses tool_use without label", () => {
    const line = JSON.stringify({
      type: "assistant",
      message: { content: [{ type: "tool_use", name: "Bash", input: {} }] },
    });
    expect(parseEvent(line)).toEqual({ kind: "tool_use", content: "Bash" });
  });

  it("parses user tool_result with string content", () => {
    const line = JSON.stringify({
      type: "user",
      message: { content: [{ type: "tool_result", content: "done" }] },
    });
    expect(parseEvent(line)).toEqual({ kind: "tool_result", content: "done" });
  });

  it("parses user tool_result with array content", () => {
    const line = JSON.stringify({
      type: "user",
      message: {
        content: [
          {
            type: "tool_result",
            content: [{ text: "line1" }, { text: "line2" }],
          },
        ],
      },
    });
    expect(parseEvent(line)).toEqual({ kind: "tool_result", content: "line1 line2" });
  });

  it("returns null for empty tool_result content", () => {
    const line = JSON.stringify({
      type: "user",
      message: { content: [{ type: "tool_result", content: "  " }] },
    });
    expect(parseEvent(line)).toBeNull();
  });

  it("parses result event", () => {
    const line = JSON.stringify({ type: "result", result: "Fixed lint errors" });
    expect(parseEvent(line)).toEqual({ kind: "result", content: "Fixed lint errors" });
  });

  it("returns null when assistant has no text or tool_use", () => {
    const line = JSON.stringify({
      type: "assistant",
      message: { content: [{ type: "unknown" }] },
    });
    expect(parseEvent(line)).toBeNull();
  });
});

describe("handleStdoutLine", () => {
  it("ignores empty lines", () => {
    const activity: string[] = [];
    const emitted: string[] = [];
    const result = handleStdoutLine("   ", activity, "prev", (a) => emitted.push(a.join("|")));
    expect(result).toBe("prev");
    expect(activity).toEqual([]);
    expect(emitted).toEqual([]);
  });

  it("appends text event and emits", () => {
    const activity: string[] = [];
    const emitted: string[] = [];
    const line = JSON.stringify({
      type: "assistant",
      message: { content: [{ type: "text", text: "hi" }] },
    });
    handleStdoutLine(line, activity, "", (a) => emitted.push(a.join("|")));
    expect(activity).toEqual(["hi"]);
    expect(emitted).toEqual(["hi"]);
  });

  it("prefixes tool_use lines with >", () => {
    const activity: string[] = [];
    const line = JSON.stringify({
      type: "assistant",
      message: {
        content: [{ type: "tool_use", name: "Bash", input: { command: "ls" } }],
      },
    });
    handleStdoutLine(line, activity, "", () => {});
    expect(activity).toEqual(["> Bash: ls"]);
  });

  it("truncates long tool_result previews", () => {
    const activity: string[] = [];
    const longContent = "x".repeat(500);
    const line = JSON.stringify({
      type: "user",
      message: { content: [{ type: "tool_result", content: longContent }] },
    });
    handleStdoutLine(line, activity, "", () => {});
    expect(activity[0]).toMatch(/^ {2}x{200}\.\.\.$/);
  });

  it("returns result content as new resultText", () => {
    const line = JSON.stringify({ type: "result", result: "final summary" });
    const result = handleStdoutLine(line, [], "prev", () => {});
    expect(result).toBe("final summary");
  });
});
