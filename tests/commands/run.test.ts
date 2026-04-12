import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { runRunCommand } from "../../src/commands/run.js";

describe("runRunCommand", () => {
  let tmp: string;

  beforeEach(() => {
    tmp = join(tmpdir(), `stitch-cmd-${Date.now()}-${Math.random().toString(36).slice(2)}`);
    mkdirSync(tmp, { recursive: true });
  });

  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
  });

  it("returns 0 when no CI config found", async () => {
    const code = await runRunCommand({
      agent: "claude",
      repo: tmp,
      maxAttempts: 3,
      output: "text",
      dryRun: false,
      failFast: false,
      push: true,
      watch: false,
      debounce: 3.0,
    });
    expect(code).toBe(0);
  });

  it("dry-run lists jobs without executing", async () => {
    writeFileSync(
      join(tmp, ".gitlab-ci.yml"),
      `
lint:
  script:
    - echo lint
test:
  script:
    - echo test
`,
    );
    const code = await runRunCommand({
      agent: "claude",
      repo: tmp,
      maxAttempts: 3,
      output: "text",
      dryRun: true,
      failFast: false,
      jobs: "lint,test",
      push: true,
      watch: false,
      debounce: 3.0,
    });
    expect(code).toBe(0);
  });

  it("json output produces valid JSON", async () => {
    writeFileSync(
      join(tmp, ".gitlab-ci.yml"),
      `
lint:
  script:
    - echo lint
`,
    );
    const originalLog = console.log;
    let output = "";
    console.log = (...args: unknown[]) => {
      output += args.map(String).join(" ") + "\n";
    };

    const code = await runRunCommand({
      agent: "claude",
      repo: tmp,
      maxAttempts: 1,
      output: "json",
      dryRun: false,
      failFast: false,
      jobs: "lint",
      push: true,
      watch: false,
      debounce: 3.0,
    });

    console.log = originalLog;

    expect(code).toBe(0);
    // Find the JSON block in output
    const jsonStart = output.indexOf("{");
    if (jsonStart >= 0) {
      const jsonEnd = output.lastIndexOf("}");
      const jsonStr = output.slice(jsonStart, jsonEnd + 1);
      const parsed = JSON.parse(jsonStr);
      expect(parsed).toHaveProperty("overall_status");
      expect(parsed).toHaveProperty("jobs");
    }
  });

  it("returns 2 for nonexistent repo path", async () => {
    const code = await runRunCommand({
      agent: "claude",
      repo: "/nonexistent/path",
      maxAttempts: 3,
      output: "text",
      dryRun: false,
      failFast: false,
      push: true,
      watch: false,
      debounce: 3.0,
    });
    expect(code).toBe(2);
  });
});
