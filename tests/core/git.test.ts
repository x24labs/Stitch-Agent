import { execSync } from "node:child_process";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { commit, snapshot } from "../../src/core/git.js";

function git(args: string, cwd: string) {
  execSync(`git ${args}`, {
    cwd,
    stdio: "ignore",
    env: {
      ...process.env,
      GIT_AUTHOR_NAME: "test",
      GIT_AUTHOR_EMAIL: "test@test.com",
      GIT_COMMITTER_NAME: "test",
      GIT_COMMITTER_EMAIL: "test@test.com",
    },
  });
}

describe("git snapshot", () => {
  let tmp: string;

  beforeEach(() => {
    tmp = join(tmpdir(), `stitch-git-${Date.now()}-${Math.random().toString(36).slice(2)}`);
    mkdirSync(tmp, { recursive: true });
    git("init -b main", tmp);
    git("config user.email test@test.com", tmp);
    git("config user.name test", tmp);
    writeFileSync(join(tmp, "file.txt"), "initial");
    git("add .", tmp);
    git("commit -m init", tmp);
  });

  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
  });

  it("detects clean working tree", () => {
    const snap = snapshot(tmp);
    expect(snap.clean).toBe(true);
    expect(snap.branch).toBe("main");
  });

  it("detects dirty working tree", () => {
    writeFileSync(join(tmp, "file.txt"), "modified");
    const snap = snapshot(tmp);
    expect(snap.clean).toBe(false);
  });

  it("detects branch name", () => {
    git("checkout -b feature/test", tmp);
    const snap = snapshot(tmp);
    expect(snap.branch).toBe("feature/test");
  });

  it("reports no remote when none configured", () => {
    const snap = snapshot(tmp);
    expect(snap.hasRemote).toBe(false);
    expect(snap.ahead).toBe(0);
  });
});

describe("git commit", () => {
  let tmp: string;

  beforeEach(() => {
    tmp = join(tmpdir(), `stitch-git-${Date.now()}-${Math.random().toString(36).slice(2)}`);
    mkdirSync(tmp, { recursive: true });
    git("init -b main", tmp);
    git("config user.email test@test.com", tmp);
    git("config user.name test", tmp);
    writeFileSync(join(tmp, "file.txt"), "initial");
    git("add .", tmp);
    git("commit -m init", tmp);
  });

  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
  });

  it("commits with fix(stitch) message", () => {
    writeFileSync(join(tmp, "file.txt"), "fixed");
    const result = commit(tmp, ["lint", "test"]);
    expect(result.ok).toBe(true);
    expect(result.message).toBe("fix(stitch): lint, test");
    expect(result.sha.length).toBeGreaterThan(0);
  });

  it("returns ok=false when nothing to commit", () => {
    const result = commit(tmp, ["lint"]);
    expect(result.ok).toBe(false);
    expect(result.message).toBe("no changes to commit");
  });
});
