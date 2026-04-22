import { execSync } from "node:child_process";
import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { commit, push, snapshot } from "../../src/core/git.js";

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
    git("init", tmp);
    git("checkout -b main", tmp);
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
    git("init", tmp);
    git("checkout -b main", tmp);
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
    expect(result.reason).toBe("ok");
  });

  it("returns ok=false when nothing to commit", () => {
    const result = commit(tmp, ["lint"]);
    expect(result.ok).toBe(false);
    expect(result.message).toBe("no changes to commit");
    expect(result.reason).toBe("nothing_staged");
  });

  it("stages and commits new (untracked) files", () => {
    writeFileSync(join(tmp, "brand-new.ts"), "export const x = 1;");
    const result = commit(tmp, ["test"]);
    expect(result.ok).toBe(true);
    expect(result.reason).toBe("ok");
    const tracked = execSync("git ls-files brand-new.ts", { cwd: tmp }).toString().trim();
    expect(tracked).toBe("brand-new.ts");
  });

  it("stages deletions", () => {
    rmSync(join(tmp, "file.txt"));
    const result = commit(tmp, ["cleanup"]);
    expect(result.ok).toBe(true);
    const tracked = execSync("git ls-files file.txt", { cwd: tmp }).toString().trim();
    expect(tracked).toBe("");
  });

  it("respects .gitignore", () => {
    writeFileSync(join(tmp, ".gitignore"), "dist/\n");
    git("add .gitignore", tmp);
    git("commit -m add-ignore", tmp);
    mkdirSync(join(tmp, "dist"), { recursive: true });
    writeFileSync(join(tmp, "dist", "bundle.js"), "ignored");
    writeFileSync(join(tmp, "tracked.ts"), "kept");
    const result = commit(tmp, ["build"]);
    expect(result.ok).toBe(true);
    const tracked = execSync("git ls-files", { cwd: tmp }).toString();
    expect(tracked).not.toContain("dist/bundle.js");
    expect(tracked).toContain("tracked.ts");
  });
});

describe("git push", () => {
  let tmp: string;
  let remote: string;

  beforeEach(() => {
    tmp = join(tmpdir(), `stitch-push-${Date.now()}-${Math.random().toString(36).slice(2)}`);
    remote = join(tmpdir(), `stitch-remote-${Date.now()}-${Math.random().toString(36).slice(2)}`);
    mkdirSync(tmp, { recursive: true });
    mkdirSync(remote, { recursive: true });
    git("init --bare", remote);
    git("init", tmp);
    git("checkout -b main", tmp);
    git("config user.email test@test.com", tmp);
    git("config user.name test", tmp);
    writeFileSync(join(tmp, "file.txt"), "initial");
    git("add .", tmp);
    git("commit -m init", tmp);
  });

  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
    rmSync(remote, { recursive: true, force: true });
  });

  it("fails when no remote is configured at all", () => {
    const result = push(tmp);
    expect(result.ok).toBe(false);
    expect(result.error.length).toBeGreaterThan(0);
  });

  it("sets upstream and pushes when remote added but no upstream set", () => {
    git(`remote add origin ${remote}`, tmp);
    const result = push(tmp);
    expect(result.ok).toBe(true);
    expect(result.error).toBe("");
  });

  it("pushes via tracked upstream", () => {
    git(`remote add origin ${remote}`, tmp);
    git("push -u origin main", tmp);
    writeFileSync(join(tmp, "file.txt"), "more");
    git("add .", tmp);
    git("commit -m followup", tmp);
    const result = push(tmp);
    expect(result.ok).toBe(true);
  });
});
