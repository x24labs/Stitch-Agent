import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { ensureStitchIgnored } from "../../src/core/gitignore.js";

describe("ensureStitchIgnored", () => {
  let tmp: string;
  let gitignore: string;

  beforeEach(() => {
    tmp = join(tmpdir(), `stitch-gitignore-${Date.now()}-${Math.random().toString(36).slice(2)}`);
    mkdirSync(tmp, { recursive: true });
    gitignore = join(tmp, ".gitignore");
  });

  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
  });

  it("creates .gitignore when absent", () => {
    const result = ensureStitchIgnored(tmp);
    expect(result.created).toBe(true);
    expect(result.added).toEqual([".stitch/", ".stitch.lock"]);
    expect(existsSync(gitignore)).toBe(true);
    const content = readFileSync(gitignore, "utf-8");
    expect(content).toContain(".stitch/");
    expect(content).toContain(".stitch.lock");
  });

  it("appends missing entries to existing .gitignore", () => {
    writeFileSync(gitignore, "node_modules/\ndist/\n");
    const result = ensureStitchIgnored(tmp);
    expect(result.created).toBe(false);
    expect(result.added).toEqual([".stitch/", ".stitch.lock"]);
    const content = readFileSync(gitignore, "utf-8");
    expect(content).toContain("node_modules/");
    expect(content).toContain("dist/");
    expect(content).toContain(".stitch/");
    expect(content).toContain(".stitch.lock");
  });

  it("is idempotent: no changes when both entries already present", () => {
    writeFileSync(gitignore, "node_modules/\n.stitch/\n.stitch.lock\n");
    const before = readFileSync(gitignore, "utf-8");
    const result = ensureStitchIgnored(tmp);
    expect(result.added).toEqual([]);
    expect(result.created).toBe(false);
    expect(readFileSync(gitignore, "utf-8")).toBe(before);
  });

  it("treats `.stitch` (no trailing slash) as covering `.stitch/`", () => {
    writeFileSync(gitignore, ".stitch\n.stitch.lock\n");
    const result = ensureStitchIgnored(tmp);
    expect(result.added).toEqual([]);
  });

  it("only adds the entry that is actually missing", () => {
    writeFileSync(gitignore, ".stitch/\n");
    const result = ensureStitchIgnored(tmp);
    expect(result.added).toEqual([".stitch.lock"]);
    const content = readFileSync(gitignore, "utf-8");
    const stitchDirCount = content.split("\n").filter((l) => l.trim() === ".stitch/").length;
    expect(stitchDirCount).toBe(1);
    expect(content).toContain(".stitch.lock");
  });

  it("ignores commented and negated lines when checking coverage", () => {
    writeFileSync(gitignore, "# .stitch/\n!.stitch/\n");
    const result = ensureStitchIgnored(tmp);
    expect(result.added).toEqual([".stitch/", ".stitch.lock"]);
  });

  it("preserves existing trailing content separation", () => {
    writeFileSync(gitignore, "node_modules/\n");
    ensureStitchIgnored(tmp);
    const content = readFileSync(gitignore, "utf-8");
    expect(content.startsWith("node_modules/\n")).toBe(true);
    expect(content).toMatch(/node_modules\/\n\n# Stitch/);
  });

  it("running twice does not duplicate entries", () => {
    ensureStitchIgnored(tmp);
    const first = readFileSync(gitignore, "utf-8");
    const result = ensureStitchIgnored(tmp);
    expect(result.added).toEqual([]);
    expect(readFileSync(gitignore, "utf-8")).toBe(first);
  });
});
