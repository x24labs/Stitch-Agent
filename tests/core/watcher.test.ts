import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  LockAcquireError,
  StitchLock,
  fileSnapshot,
  shouldIgnore,
} from "../../src/core/watcher.js";

describe("shouldIgnore", () => {
  it("ignores node_modules", () => {
    expect(shouldIgnore("/repo/node_modules/foo.js", "/repo")).toBe(true);
  });

  it("ignores .git directory", () => {
    expect(shouldIgnore("/repo/.git/config", "/repo")).toBe(true);
  });

  it("ignores hidden files", () => {
    expect(shouldIgnore("/repo/.secret", "/repo")).toBe(true);
  });

  it("keeps .gitlab-ci.yml", () => {
    expect(shouldIgnore("/repo/.gitlab-ci.yml", "/repo")).toBe(false);
  });

  it("keeps .gitignore", () => {
    expect(shouldIgnore("/repo/.gitignore", "/repo")).toBe(false);
  });

  it("keeps .github directory contents", () => {
    expect(shouldIgnore("/repo/.github/workflows/ci.yml", "/repo")).toBe(false);
  });

  it("ignores .stitch.lock", () => {
    expect(shouldIgnore("/repo/.stitch.lock", "/repo")).toBe(true);
  });

  it("keeps normal files", () => {
    expect(shouldIgnore("/repo/src/index.ts", "/repo")).toBe(false);
  });

  it("ignores __pycache__", () => {
    expect(shouldIgnore("/repo/__pycache__/foo.pyc", "/repo")).toBe(true);
  });

  it("ignores dist directory", () => {
    expect(shouldIgnore("/repo/dist/index.js", "/repo")).toBe(true);
  });
});

describe("fileSnapshot", () => {
  let tmp: string;

  beforeEach(() => {
    tmp = join(tmpdir(), `stitch-snap-${Date.now()}-${Math.random().toString(36).slice(2)}`);
    mkdirSync(tmp, { recursive: true });
  });

  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
  });

  it("includes normal files", () => {
    writeFileSync(join(tmp, "index.ts"), "export {}");
    const snap = fileSnapshot(tmp);
    expect(snap.has("index.ts")).toBe(true);
  });

  it("excludes node_modules", () => {
    mkdirSync(join(tmp, "node_modules", "foo"), { recursive: true });
    writeFileSync(join(tmp, "node_modules", "foo", "index.js"), "");
    const snap = fileSnapshot(tmp);
    expect([...snap.keys()].some((k) => k.includes("node_modules"))).toBe(false);
  });

  it("excludes hidden files except allowlist", () => {
    writeFileSync(join(tmp, ".secret"), "hidden");
    writeFileSync(join(tmp, ".gitignore"), "dist/");
    const snap = fileSnapshot(tmp);
    expect(snap.has(".secret")).toBe(false);
    expect(snap.has(".gitignore")).toBe(true);
  });

  it("detects file changes via mtime/size", () => {
    writeFileSync(join(tmp, "file.txt"), "v1");
    const snap1 = fileSnapshot(tmp);
    // Force a different mtime by waiting a tiny bit
    const entry1 = snap1.get("file.txt");
    expect(entry1).toBeDefined();

    writeFileSync(join(tmp, "file.txt"), "version two, longer");
    const snap2 = fileSnapshot(tmp);
    const entry2 = snap2.get("file.txt");
    expect(entry2).toBeDefined();
    // Size should differ
    expect(entry2![1]).not.toBe(entry1![1]);
  });
});

describe("StitchLock", () => {
  let tmp: string;

  beforeEach(() => {
    tmp = join(tmpdir(), `stitch-lock-${Date.now()}-${Math.random().toString(36).slice(2)}`);
    mkdirSync(tmp, { recursive: true });
  });

  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
  });

  it("acquires and releases lock", () => {
    const lock = new StitchLock(tmp);
    lock.acquire();
    // Should not throw on release
    lock.release();
  });

  it("takes over stale lock", () => {
    // Write a lock with a definitely-dead PID
    writeFileSync(join(tmp, ".stitch.lock"), "999999999");
    const lock = new StitchLock(tmp);
    // Should not throw because the PID is dead
    expect(() => lock.acquire()).not.toThrow();
    lock.release();
  });

  it("throws on live lock", () => {
    // Lock with our own PID (definitely alive)
    writeFileSync(join(tmp, ".stitch.lock"), String(process.pid));
    const lock = new StitchLock(tmp);
    expect(() => lock.acquire()).toThrow(LockAcquireError);
  });

  it("only releases own lock", () => {
    const lock1 = new StitchLock(tmp);
    lock1.acquire();
    // Simulate another process overwriting the lock
    writeFileSync(join(tmp, ".stitch.lock"), "12345");
    lock1.release();
    // Lock file should still exist (not owned by us anymore)
    const { existsSync } = require("node:fs");
    expect(existsSync(join(tmp, ".stitch.lock"))).toBe(true);
  });
});
