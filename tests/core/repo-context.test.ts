import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { analyzeRepo } from "../../src/core/repo-context.js";

describe("analyzeRepo", () => {
  let tmp: string;

  beforeEach(() => {
    tmp = join(tmpdir(), `stitch-repo-${Date.now()}-${Math.random().toString(36).slice(2)}`);
    mkdirSync(tmp, { recursive: true });
  });

  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
  });

  it("detects Python project", () => {
    writeFileSync(join(tmp, "pyproject.toml"), '[project]\nname = "foo"');
    const ctx = analyzeRepo(tmp);
    expect(ctx.languages).toContain("python");
    expect(ctx.packageManager).toBe("uv");
  });

  it("detects JavaScript project", () => {
    writeFileSync(join(tmp, "package.json"), '{"name": "foo"}');
    const ctx = analyzeRepo(tmp);
    expect(ctx.languages).toContain("javascript");
  });

  it("detects multi-language project", () => {
    writeFileSync(join(tmp, "pyproject.toml"), '[project]\nname = "foo"');
    writeFileSync(join(tmp, "package.json"), '{"name": "foo"}');
    const ctx = analyzeRepo(tmp);
    expect(ctx.languages).toContain("python");
    expect(ctx.languages).toContain("javascript");
  });

  it("detects package manager from lock files", () => {
    writeFileSync(join(tmp, "package.json"), '{"name": "foo"}');
    writeFileSync(join(tmp, "bun.lockb"), "");
    const ctx = analyzeRepo(tmp);
    expect(ctx.packageManager).toBe("bun");
  });

  it("detects Go project", () => {
    writeFileSync(join(tmp, "go.mod"), "module foo");
    const ctx = analyzeRepo(tmp);
    expect(ctx.languages).toContain("go");
    expect(ctx.packageManager).toBe("go");
  });

  it("detects Rust project", () => {
    writeFileSync(join(tmp, "Cargo.toml"), '[package]\nname = "foo"');
    const ctx = analyzeRepo(tmp);
    expect(ctx.languages).toContain("rust");
    expect(ctx.packageManager).toBe("cargo");
  });

  it("detects TypeScript framework from tsconfig", () => {
    writeFileSync(join(tmp, "tsconfig.json"), "{}");
    const ctx = analyzeRepo(tmp);
    expect(ctx.frameworks).toContain("typescript");
  });

  it("detects pytest from pyproject.toml", () => {
    writeFileSync(join(tmp, "pyproject.toml"), '[project.optional-dependencies]\ndev = ["pytest"]');
    const ctx = analyzeRepo(tmp);
    expect(ctx.frameworks).toContain("pytest");
  });

  it("detects GitLab CI platform", () => {
    writeFileSync(join(tmp, ".gitlab-ci.yml"), "test:\n  script: [echo]");
    const ctx = analyzeRepo(tmp);
    expect(ctx.ciPlatform).toBe("gitlab");
    expect(ctx.existingCIFile).toBe(".gitlab-ci.yml");
  });

  it("detects GitHub CI platform", () => {
    mkdirSync(join(tmp, ".github", "workflows"), { recursive: true });
    writeFileSync(join(tmp, ".github", "workflows", "ci.yml"), "name: CI");
    const ctx = analyzeRepo(tmp);
    expect(ctx.ciPlatform).toBe("github");
  });

  it("detects frameworks from package.json deps", () => {
    writeFileSync(
      join(tmp, "package.json"),
      '{"devDependencies": {"vitest": "^1.0.0", "typescript": "^5.0.0"}}',
    );
    const ctx = analyzeRepo(tmp);
    expect(ctx.frameworks).toContain("vitest");
    expect(ctx.frameworks).toContain("typescript");
  });

  it("collects entry files", () => {
    writeFileSync(join(tmp, "package.json"), '{"name": "foo"}');
    writeFileSync(join(tmp, "tsconfig.json"), "{}");
    writeFileSync(join(tmp, "Makefile"), "all:");
    const ctx = analyzeRepo(tmp);
    expect(ctx.entryFiles).toContain("package.json");
    expect(ctx.entryFiles).toContain("tsconfig.json");
    expect(ctx.entryFiles).toContain("Makefile");
  });
});
