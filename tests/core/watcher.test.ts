import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  type CmdlineProbe,
  LockAcquireError,
  type LockfileV1,
  type PidChecker,
  type Signaler,
  StitchLock,
  type StitchLockOptions,
  decide,
  fileSnapshot,
  parseLockfile,
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

  it("ignores .stitch.lock.tmp", () => {
    expect(shouldIgnore("/repo/.stitch.lock.tmp", "/repo")).toBe(true);
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
    const entry1 = snap1.get("file.txt");
    expect(entry1).toBeDefined();

    writeFileSync(join(tmp, "file.txt"), "version two, longer");
    const snap2 = fileSnapshot(tmp);
    const entry2 = snap2.get("file.txt");
    expect(entry2).toBeDefined();
    expect(entry2?.[1]).not.toBe(entry1?.[1]);
  });
});

// --------------------------------------------------------------------------
// StitchLock
// --------------------------------------------------------------------------

const STITCH_CMDLINE = "/usr/bin/node /home/user/.bun/bin/stitch run --watch";
const NON_STITCH_CMDLINE = "/bin/bash";

type Deps = {
  now: () => number;
  pidAlive: PidChecker;
  probeCmdline: CmdlineProbe;
  signal?: Signaler;
};

function mkLock(repoRoot: string, deps: Deps, overrides: Partial<StitchLockOptions> = {}) {
  return new StitchLock(repoRoot, {
    now: deps.now,
    pidAlive: deps.pidAlive,
    probeCmdline: deps.probeCmdline,
    signal: deps.signal,
    registerSignals: false,
    ...overrides,
  });
}

function writeLegacy(path: string, pid: number): void {
  writeFileSync(path, String(pid));
}

function writeV1(path: string, data: LockfileV1): void {
  writeFileSync(path, JSON.stringify(data));
}

describe("parseLockfile", () => {
  let tmp: string;
  let path: string;

  beforeEach(() => {
    tmp = join(tmpdir(), `stitch-parse-${Date.now()}-${Math.random().toString(36).slice(2)}`);
    mkdirSync(tmp, { recursive: true });
    path = join(tmp, ".stitch.lock");
  });

  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
  });

  it("parses v1 JSON", () => {
    const data: LockfileV1 = {
      version: 1,
      pid: 42,
      cmdline: "stitch",
      startedAt: 1000,
      heartbeatAt: 2000,
    };
    writeV1(path, data);
    const parsed = parseLockfile(path);
    expect(parsed.kind).toBe("v1");
    if (parsed.kind === "v1") expect(parsed.data.pid).toBe(42);
  });

  it("parses legacy plain integer", () => {
    writeLegacy(path, 12345);
    const parsed = parseLockfile(path);
    expect(parsed.kind).toBe("legacy");
    if (parsed.kind === "legacy") expect(parsed.pid).toBe(12345);
  });

  it("returns empty for empty file", () => {
    writeFileSync(path, "");
    const parsed = parseLockfile(path);
    expect(parsed.kind).toBe("empty");
  });

  it("returns empty for whitespace-only file", () => {
    writeFileSync(path, "   \n  ");
    const parsed = parseLockfile(path);
    expect(parsed.kind).toBe("empty");
  });

  it("returns malformed for partial JSON", () => {
    writeFileSync(path, "{partial");
    const parsed = parseLockfile(path);
    expect(parsed.kind).toBe("malformed");
  });

  it("returns malformed for JSON with wrong shape", () => {
    writeFileSync(path, JSON.stringify({ version: 2, pid: 1 }));
    const parsed = parseLockfile(path);
    expect(parsed.kind).toBe("malformed");
  });

  it("returns malformed for non-JSON non-integer", () => {
    writeFileSync(path, "hello");
    const parsed = parseLockfile(path);
    expect(parsed.kind).toBe("malformed");
  });
});

describe("decide", () => {
  const now = () => 1_000_000;

  it("reclaims on malformed", () => {
    expect(
      decide(
        { kind: "malformed" },
        {
          now,
          pidAlive: () => true,
          probeCmdline: () => STITCH_CMDLINE,
          heartbeatStaleMs: 30_000,
          legacyStaleAgeMs: 600_000,
        },
      ),
    ).toBe("reclaim");
  });

  it("reclaims on empty", () => {
    expect(
      decide(
        { kind: "empty" },
        {
          now,
          pidAlive: () => true,
          probeCmdline: () => STITCH_CMDLINE,
          heartbeatStaleMs: 30_000,
          legacyStaleAgeMs: 600_000,
        },
      ),
    ).toBe("reclaim");
  });

  it("legacy: reclaims when pid dead", () => {
    expect(
      decide(
        { kind: "legacy", pid: 42, mtime: now() - 1000 },
        {
          now,
          pidAlive: () => false,
          probeCmdline: () => STITCH_CMDLINE,
          heartbeatStaleMs: 30_000,
          legacyStaleAgeMs: 600_000,
        },
      ),
    ).toBe("reclaim");
  });

  it("legacy: reclaims when cmdline not stitch", () => {
    expect(
      decide(
        { kind: "legacy", pid: 42, mtime: now() - 1000 },
        {
          now,
          pidAlive: () => true,
          probeCmdline: () => NON_STITCH_CMDLINE,
          heartbeatStaleMs: 30_000,
          legacyStaleAgeMs: 600_000,
        },
      ),
    ).toBe("reclaim");
  });

  it("legacy: reclaims when mtime > legacyStaleAgeMs", () => {
    expect(
      decide(
        { kind: "legacy", pid: 42, mtime: now() - 700_000 },
        {
          now,
          pidAlive: () => true,
          probeCmdline: () => STITCH_CMDLINE,
          heartbeatStaleMs: 30_000,
          legacyStaleAgeMs: 600_000,
        },
      ),
    ).toBe("reclaim");
  });

  it("legacy: blocks when fresh, alive, stitch-like", () => {
    expect(
      decide(
        { kind: "legacy", pid: 42, mtime: now() - 10_000 },
        {
          now,
          pidAlive: () => true,
          probeCmdline: () => STITCH_CMDLINE,
          heartbeatStaleMs: 30_000,
          legacyStaleAgeMs: 600_000,
        },
      ),
    ).toBe("block");
  });

  it("v1: reclaims when pid dead", () => {
    expect(
      decide(
        {
          kind: "v1",
          data: {
            version: 1,
            pid: 42,
            cmdline: STITCH_CMDLINE,
            startedAt: 0,
            heartbeatAt: now() - 1000,
          },
        },
        {
          now,
          pidAlive: () => false,
          probeCmdline: () => STITCH_CMDLINE,
          heartbeatStaleMs: 30_000,
          legacyStaleAgeMs: 600_000,
        },
      ),
    ).toBe("reclaim");
  });

  it("v1: reclaims when cmdline mismatches and not stitch", () => {
    expect(
      decide(
        {
          kind: "v1",
          data: {
            version: 1,
            pid: 42,
            cmdline: STITCH_CMDLINE,
            startedAt: 0,
            heartbeatAt: now() - 1000,
          },
        },
        {
          now,
          pidAlive: () => true,
          probeCmdline: () => NON_STITCH_CMDLINE,
          heartbeatStaleMs: 30_000,
          legacyStaleAgeMs: 600_000,
        },
      ),
    ).toBe("reclaim");
  });

  it("v1: terminates when heartbeat stale", () => {
    expect(
      decide(
        {
          kind: "v1",
          data: {
            version: 1,
            pid: 42,
            cmdline: STITCH_CMDLINE,
            startedAt: 0,
            heartbeatAt: now() - 60_000,
          },
        },
        {
          now,
          pidAlive: () => true,
          probeCmdline: () => STITCH_CMDLINE,
          heartbeatStaleMs: 30_000,
          legacyStaleAgeMs: 600_000,
        },
      ),
    ).toBe("terminate");
  });

  it("v1: blocks when fresh heartbeat", () => {
    expect(
      decide(
        {
          kind: "v1",
          data: {
            version: 1,
            pid: 42,
            cmdline: STITCH_CMDLINE,
            startedAt: 0,
            heartbeatAt: now() - 5_000,
          },
        },
        {
          now,
          pidAlive: () => true,
          probeCmdline: () => STITCH_CMDLINE,
          heartbeatStaleMs: 30_000,
          legacyStaleAgeMs: 600_000,
        },
      ),
    ).toBe("block");
  });

  it("v1: blocks when cmdline probe returns null (cannot prove recycled)", () => {
    expect(
      decide(
        {
          kind: "v1",
          data: {
            version: 1,
            pid: 42,
            cmdline: STITCH_CMDLINE,
            startedAt: 0,
            heartbeatAt: now() - 5_000,
          },
        },
        {
          now,
          pidAlive: () => true,
          probeCmdline: () => null,
          heartbeatStaleMs: 30_000,
          legacyStaleAgeMs: 600_000,
        },
      ),
    ).toBe("block");
  });
});

describe("StitchLock acquire/release", () => {
  let tmp: string;
  let lockPath: string;

  beforeEach(() => {
    tmp = join(tmpdir(), `stitch-lock-${Date.now()}-${Math.random().toString(36).slice(2)}`);
    mkdirSync(tmp, { recursive: true });
    lockPath = join(tmp, ".stitch.lock");
  });

  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
  });

  it("fresh acquire writes v1 JSON with required fields", () => {
    const lock = mkLock(tmp, {
      now: () => 1_000_000,
      pidAlive: () => false,
      probeCmdline: () => STITCH_CMDLINE,
    });
    lock.acquire();
    const body = JSON.parse(readFileSync(lockPath, "utf-8"));
    expect(body.version).toBe(1);
    expect(body.pid).toBe(process.pid);
    expect(typeof body.cmdline).toBe("string");
    expect(body.startedAt).toBe(1_000_000);
    expect(body.heartbeatAt).toBe(1_000_000);
    lock.release();
  });

  it("acquire reclaims lockfile with dead pid", () => {
    writeV1(lockPath, {
      version: 1,
      pid: 42,
      cmdline: STITCH_CMDLINE,
      startedAt: 0,
      heartbeatAt: 0,
    });
    const lock = mkLock(tmp, {
      now: () => 1_000_000,
      pidAlive: () => false,
      probeCmdline: () => STITCH_CMDLINE,
    });
    expect(() => lock.acquire()).not.toThrow();
    const body = JSON.parse(readFileSync(lockPath, "utf-8"));
    expect(body.pid).toBe(process.pid);
    lock.release();
  });

  it("acquire reclaims lockfile with recycled pid", () => {
    writeV1(lockPath, {
      version: 1,
      pid: 42,
      cmdline: STITCH_CMDLINE,
      startedAt: 0,
      heartbeatAt: 0,
    });
    const lock = mkLock(tmp, {
      now: () => 1_000_000,
      pidAlive: () => true,
      probeCmdline: () => NON_STITCH_CMDLINE,
    });
    expect(() => lock.acquire()).not.toThrow();
    const body = JSON.parse(readFileSync(lockPath, "utf-8"));
    expect(body.pid).toBe(process.pid);
    lock.release();
  });

  it("acquire terminates hung Stitch via SIGTERM", () => {
    writeV1(lockPath, {
      version: 1,
      pid: 42,
      cmdline: STITCH_CMDLINE,
      startedAt: 0,
      heartbeatAt: 0,
    });
    let alive = true;
    const sigCalls: Array<[number, NodeJS.Signals | 0]> = [];
    const lock = mkLock(tmp, {
      now: () => 1_000_000,
      pidAlive: () => alive,
      probeCmdline: () => STITCH_CMDLINE,
      signal: (pid, sig) => {
        sigCalls.push([pid, sig]);
        if (sig === "SIGTERM") alive = false;
      },
    });
    expect(() => lock.acquire()).not.toThrow();
    expect(sigCalls[0]).toEqual([42, "SIGTERM"]);
    expect(sigCalls.find((c) => c[1] === "SIGKILL")).toBeUndefined();
    lock.release();
  });

  it("acquire escalates to SIGKILL when SIGTERM ignored", () => {
    writeV1(lockPath, {
      version: 1,
      pid: 42,
      cmdline: STITCH_CMDLINE,
      startedAt: 0,
      heartbeatAt: 0,
    });
    let alive = true;
    const sigCalls: Array<NodeJS.Signals | 0> = [];
    const lock = mkLock(
      tmp,
      {
        now: () => 1_000_000,
        pidAlive: () => alive,
        probeCmdline: () => STITCH_CMDLINE,
        signal: (_pid, sig) => {
          sigCalls.push(sig);
          if (sig === "SIGKILL") alive = false;
        },
      },
      { termGraceMs: 50, killGraceMs: 50 },
    );
    expect(() => lock.acquire()).not.toThrow();
    expect(sigCalls).toContain("SIGTERM");
    expect(sigCalls).toContain("SIGKILL");
    lock.release();
  });

  it("acquire throws LockAcquireError with SIGKILL message when termination fully fails", () => {
    writeV1(lockPath, {
      version: 1,
      pid: 42,
      cmdline: STITCH_CMDLINE,
      startedAt: 0,
      heartbeatAt: 0,
    });
    const lock = mkLock(
      tmp,
      {
        now: () => 1_000_000,
        pidAlive: () => true,
        probeCmdline: () => STITCH_CMDLINE,
        signal: () => {
          /* ignored */
        },
      },
      { termGraceMs: 20, killGraceMs: 20 },
    );
    try {
      lock.acquire();
      throw new Error("acquire should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(LockAcquireError);
      expect((err as Error).message).toMatch(/SIGKILL/);
      expect((err as Error).message).toMatch(/42/);
    }
  });

  it("acquire blocks on fresh heartbeat without 'delete' hint in message", () => {
    writeV1(lockPath, {
      version: 1,
      pid: 42,
      cmdline: STITCH_CMDLINE,
      startedAt: 0,
      heartbeatAt: 999_000,
    });
    const lock = mkLock(tmp, {
      now: () => 1_000_000,
      pidAlive: () => true,
      probeCmdline: () => STITCH_CMDLINE,
    });
    try {
      lock.acquire();
      throw new Error("acquire should have thrown");
    } catch (err) {
      expect(err).toBeInstanceOf(LockAcquireError);
      const msg = (err as Error).message;
      expect(msg).toMatch(/42/);
      expect(msg.toLowerCase()).not.toContain("delete");
      expect(msg).not.toContain(".stitch.lock");
    }
  });

  it("acquire reclaims legacy lockfile with dead pid", () => {
    writeLegacy(lockPath, 999_999_999);
    const lock = mkLock(tmp, {
      now: () => 1_000_000,
      pidAlive: () => false,
      probeCmdline: () => STITCH_CMDLINE,
    });
    expect(() => lock.acquire()).not.toThrow();
    const body = JSON.parse(readFileSync(lockPath, "utf-8"));
    expect(body.version).toBe(1);
    expect(body.pid).toBe(process.pid);
    lock.release();
  });

  it("acquire reclaims legacy lockfile with recycled pid", () => {
    writeLegacy(lockPath, 42);
    const lock = mkLock(tmp, {
      now: () => 1_000_000,
      pidAlive: () => true,
      probeCmdline: () => NON_STITCH_CMDLINE,
    });
    expect(() => lock.acquire()).not.toThrow();
    lock.release();
  });

  it("acquire reclaims legacy lockfile with old mtime", async () => {
    writeLegacy(lockPath, 42);
    // Force mtime into the past
    const past = (Date.now() - 700_000) / 1000;
    (await import("node:fs")).utimesSync(lockPath, past, past);
    const lock = mkLock(tmp, {
      now: () => Date.now(),
      pidAlive: () => true,
      probeCmdline: () => STITCH_CMDLINE,
    });
    expect(() => lock.acquire()).not.toThrow();
    lock.release();
  });

  it("acquire blocks legacy lockfile with fresh mtime + stitch cmdline", () => {
    writeLegacy(lockPath, 42);
    const lock = mkLock(tmp, {
      now: () => Date.now(),
      pidAlive: () => true,
      probeCmdline: () => STITCH_CMDLINE,
    });
    expect(() => lock.acquire()).toThrow(LockAcquireError);
  });

  it("acquire reclaims malformed lockfile", () => {
    writeFileSync(lockPath, "{not-json");
    const lock = mkLock(tmp, {
      now: () => 1_000_000,
      pidAlive: () => true,
      probeCmdline: () => STITCH_CMDLINE,
    });
    expect(() => lock.acquire()).not.toThrow();
    lock.release();
  });

  it("acquire reclaims empty lockfile", () => {
    writeFileSync(lockPath, "");
    const lock = mkLock(tmp, {
      now: () => 1_000_000,
      pidAlive: () => true,
      probeCmdline: () => STITCH_CMDLINE,
    });
    expect(() => lock.acquire()).not.toThrow();
    lock.release();
  });

  it("release is idempotent", () => {
    const lock = mkLock(tmp, {
      now: () => 1_000_000,
      pidAlive: () => false,
      probeCmdline: () => STITCH_CMDLINE,
    });
    lock.acquire();
    lock.release();
    expect(() => lock.release()).not.toThrow();
    expect(existsSync(lockPath)).toBe(false);
  });

  it("release does not unlink someone else's lock", () => {
    const lock = mkLock(tmp, {
      now: () => 1_000_000,
      pidAlive: () => false,
      probeCmdline: () => STITCH_CMDLINE,
    });
    lock.acquire();
    // Simulate another process replacing our lockfile
    writeV1(lockPath, {
      version: 1,
      pid: 12345,
      cmdline: STITCH_CMDLINE,
      startedAt: 0,
      heartbeatAt: 0,
    });
    lock.release();
    expect(existsSync(lockPath)).toBe(true);
    const body = JSON.parse(readFileSync(lockPath, "utf-8"));
    expect(body.pid).toBe(12345);
  });

  it("heartbeat advances heartbeatAt via rename", async () => {
    let t = 1_000_000;
    const lock = mkLock(
      tmp,
      {
        now: () => t,
        pidAlive: () => false,
        probeCmdline: () => STITCH_CMDLINE,
      },
      { heartbeatIntervalMs: 10 },
    );
    lock.acquire();
    const first = JSON.parse(readFileSync(lockPath, "utf-8"));
    expect(first.heartbeatAt).toBe(1_000_000);
    t = 1_006_000;
    await new Promise((resolve) => setTimeout(resolve, 50));
    const second = JSON.parse(readFileSync(lockPath, "utf-8"));
    expect(second.heartbeatAt).toBeGreaterThan(first.heartbeatAt);
    lock.release();
  });
});
