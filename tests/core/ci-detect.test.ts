import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { detectPlatform } from "../../src/core/ci-detect.js";

describe("detectPlatform", () => {
  let tmp: string;

  beforeEach(() => {
    tmp = join(tmpdir(), `stitch-test-${Date.now()}-${Math.random().toString(36).slice(2)}`);
    mkdirSync(tmp, { recursive: true });
    // Clean env
    delete process.env.GITLAB_CI;
    delete process.env.GITHUB_ACTIONS;
  });

  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
    delete process.env.GITLAB_CI;
    delete process.env.GITHUB_ACTIONS;
  });

  it("detects GitLab from env var", () => {
    process.env.GITLAB_CI = "true";
    expect(detectPlatform()).toBe("gitlab");
  });

  it("detects GitHub from env var", () => {
    process.env.GITHUB_ACTIONS = "true";
    expect(detectPlatform()).toBe("github");
  });

  it("prefers env var over file detection", () => {
    process.env.GITLAB_CI = "true";
    mkdirSync(join(tmp, ".github", "workflows"), { recursive: true });
    expect(detectPlatform(tmp)).toBe("gitlab");
  });

  it("detects GitLab from file", () => {
    writeFileSync(join(tmp, ".gitlab-ci.yml"), "stages: [test]");
    expect(detectPlatform(tmp)).toBe("gitlab");
  });

  it("detects GitHub from directory", () => {
    mkdirSync(join(tmp, ".github", "workflows"), { recursive: true });
    expect(detectPlatform(tmp)).toBe("github");
  });

  it("returns unknown when both exist", () => {
    writeFileSync(join(tmp, ".gitlab-ci.yml"), "stages: [test]");
    mkdirSync(join(tmp, ".github", "workflows"), { recursive: true });
    expect(detectPlatform(tmp)).toBe("unknown");
  });

  it("returns unknown when neither exists", () => {
    expect(detectPlatform(tmp)).toBe("unknown");
  });

  it("returns unknown without repoRoot", () => {
    expect(detectPlatform()).toBe("unknown");
  });
});
