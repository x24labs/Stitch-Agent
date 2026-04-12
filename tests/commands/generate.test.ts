import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { runGenerateCommand } from "../../src/commands/generate.js";

describe("runGenerateCommand", () => {
  let tmp: string;

  beforeEach(() => {
    tmp = join(tmpdir(), `stitch-gen-${Date.now()}-${Math.random().toString(36).slice(2)}`);
    mkdirSync(tmp, { recursive: true });
  });

  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
  });

  it("dry-run shows repo analysis without LLM call", async () => {
    writeFileSync(join(tmp, "package.json"), '{"name":"test","devDependencies":{"vitest":"^1.0"}}');
    writeFileSync(join(tmp, "tsconfig.json"), "{}");

    const code = await runGenerateCommand({
      agent: "claude",
      repo: tmp,
      output: "text",
      dryRun: true,
    });
    expect(code).toBe(0);
  });

  it("dry-run with json output", async () => {
    writeFileSync(join(tmp, "pyproject.toml"), '[project]\nname = "foo"');

    const originalLog = console.log;
    let output = "";
    console.log = (...args: unknown[]) => {
      output += args.map(String).join(" ") + "\n";
    };

    const code = await runGenerateCommand({
      agent: "claude",
      repo: tmp,
      output: "json",
      dryRun: true,
    });

    console.log = originalLog;
    expect(code).toBe(0);

    const jsonStart = output.indexOf("{");
    if (jsonStart >= 0) {
      const jsonEnd = output.lastIndexOf("}");
      const jsonStr = output.slice(jsonStart, jsonEnd + 1);
      const parsed = JSON.parse(jsonStr);
      expect(parsed).toHaveProperty("languages");
    }
  });

  it("returns 2 for nonexistent repo path", async () => {
    const code = await runGenerateCommand({
      agent: "claude",
      repo: "/nonexistent/path",
      output: "text",
      dryRun: false,
    });
    expect(code).toBe(2);
  });
});
