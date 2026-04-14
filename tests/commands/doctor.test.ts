import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { runDoctorCommand } from "../../src/commands/doctor.js";

describe("runDoctorCommand", () => {
  let tmp: string;
  let captured: string;
  let originalWrite: typeof process.stdout.write;

  beforeEach(() => {
    tmp = join(tmpdir(), `stitch-doctor-${Date.now()}-${Math.random().toString(36).slice(2)}`);
    mkdirSync(tmp, { recursive: true });
    captured = "";
    originalWrite = process.stdout.write.bind(process.stdout);
    process.stdout.write = ((chunk: string | Uint8Array) => {
      captured += typeof chunk === "string" ? chunk : Buffer.from(chunk).toString();
      return true;
    }) as typeof process.stdout.write;
  });

  afterEach(() => {
    process.stdout.write = originalWrite;
    rmSync(tmp, { recursive: true, force: true });
  });

  it("returns 2 for nonexistent repo path", async () => {
    const code = await runDoctorCommand({ repo: "/nonexistent/xyz", output: "text" });
    expect(code).toBe(2);
  });

  it("reports failures for an empty directory (no git, no CI)", async () => {
    const code = await runDoctorCommand({ repo: tmp, output: "json" });
    expect(code).toBe(1);
    const parsed = JSON.parse(captured);
    const byName = Object.fromEntries(
      parsed.checks.map((c: { name: string; status: string }) => [c.name, c.status]),
    );
    expect(byName["Git repository"]).toBe("fail");
    expect(byName["CI config"]).toBe("fail");
    expect(byName["Jobs parseable"]).toBe("skip");
  });

  it("passes CI config and jobs parseable when .gitlab-ci.yml has jobs", async () => {
    mkdirSync(join(tmp, ".git"), { recursive: true });
    writeFileSync(
      join(tmp, ".gitlab-ci.yml"),
      "stages:\n  - test\n\nlint:\n  stage: test\n  script:\n    - echo hi\n",
    );

    const code = await runDoctorCommand({ repo: tmp, output: "json" });
    const parsed = JSON.parse(captured);
    const byName = Object.fromEntries(
      parsed.checks.map((c: { name: string; status: string }) => [c.name, c.status]),
    );
    expect(byName["Git repository"]).toBe("pass");
    expect(byName["CI config"]).toBe("pass");
    expect(byName["Jobs parseable"]).toBe("pass");
    // code depends on agent CLI availability; just verify it's 0 or 1
    expect([0, 1]).toContain(code);
  });

  it("text output includes STITCH branding and a summary line", async () => {
    const code = await runDoctorCommand({ repo: tmp, output: "text" });
    expect(code).toBe(1);
    // Strip ANSI to assert on content
    const plain = captured.replace(/\x1b\[[0-9;]*m/g, "");
    expect(plain).toContain("STITCH");
    expect(plain).toContain("failed");
  });
});
