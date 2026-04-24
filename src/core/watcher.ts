import { execFileSync } from "node:child_process";
import {
  existsSync,
  readFileSync,
  readdirSync,
  renameSync,
  statSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { join, relative } from "node:path";
import { registerRelease } from "./lock-signals.js";

const IGNORE_DIRS = new Set([
  ".git",
  ".venv",
  "venv",
  "env",
  ".env",
  "node_modules",
  "__pycache__",
  ".pytest_cache",
  ".mypy_cache",
  ".ruff_cache",
  ".tox",
  ".nox",
  "dist",
  "build",
  "target",
  ".next",
  ".nuxt",
  ".svelte-kit",
  ".turbo",
  ".cache",
  "coverage",
  ".coverage",
  "htmlcov",
  ".idea",
  ".vscode",
  ".DS_Store",
  ".stitch",
  ".eggs",
  "*.egg-info",
  ".pyright",
  ".angular",
]);

const IGNORE_FILES = new Set([".stitch.lock", ".stitch.lock.tmp", ".DS_Store", "Thumbs.db"]);

const KEEP_HIDDEN = new Set([".gitlab-ci.yml", ".github", ".gitignore"]);

function isIgnoredPart(part: string): boolean {
  if (IGNORE_DIRS.has(part)) return true;
  if (KEEP_HIDDEN.has(part)) return false;
  return part.startsWith(".") && part !== "." && part !== "..";
}

export function shouldIgnore(filePath: string, repoRoot: string): boolean {
  let rel: string;
  try {
    rel = relative(repoRoot, filePath);
  } catch {
    return true;
  }
  const parts = rel.split("/").filter((p) => p.length > 0);
  const last = parts[parts.length - 1];
  if (!last) return false;

  for (const part of parts.slice(0, -1)) {
    if (isIgnoredPart(part)) return true;
  }

  if (IGNORE_FILES.has(last)) return true;
  if (KEEP_HIDDEN.has(last)) return false;
  return last.startsWith(".");
}

function isTrackedFile(name: string): boolean {
  if (IGNORE_FILES.has(name)) return false;
  if (name.startsWith(".") && !KEEP_HIDDEN.has(name)) return false;
  return true;
}

function scanDirectory(
  dir: string,
  repoRoot: string,
  snap: Map<string, [number, number]>,
  stack: string[],
): void {
  let entries: string[];
  try {
    entries = readdirSync(dir);
  } catch {
    return;
  }
  for (const name of entries) {
    const full = join(dir, name);
    try {
      const st = statSync(full);
      if (st.isDirectory()) {
        if (!isIgnoredPart(name)) stack.push(full);
      } else if (st.isFile() && isTrackedFile(name)) {
        snap.set(relative(repoRoot, full), [st.mtimeMs, st.size]);
      }
    } catch {}
  }
}

export function fileSnapshot(repoRoot: string): Map<string, [number, number]> {
  const snap = new Map<string, [number, number]>();
  const stack = [repoRoot];
  while (stack.length > 0) {
    const current = stack.pop();
    if (current === undefined) break;
    scanDirectory(current, repoRoot, snap, stack);
  }
  return snap;
}

export interface WatchConfig {
  debounceSeconds: number;
  pollInterval: number;
}

const DEFAULT_WATCH_CONFIG: WatchConfig = {
  debounceSeconds: 30,
  pollInterval: 1.0,
};

function snapshotsEqual(
  a: Map<string, [number, number]>,
  b: Map<string, [number, number]>,
): boolean {
  if (a.size !== b.size) return false;
  for (const [key, [mtime, size]] of a) {
    const bVal = b.get(key);
    if (!bVal || bVal[0] !== mtime || bVal[1] !== size) return false;
  }
  return true;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export class AbortedError extends Error {
  constructor() {
    super("aborted");
    this.name = "AbortedError";
  }
}

export async function waitForChangeThenIdle(
  repoRoot: string,
  config?: Partial<WatchConfig>,
  signal?: AbortSignal,
): Promise<void> {
  const cfg = { ...DEFAULT_WATCH_CONFIG, ...config };
  const baseline = fileSnapshot(repoRoot);
  let current = baseline;

  const checkAbort = () => {
    if (signal?.aborted) throw new AbortedError();
  };

  // Phase 1: wait for any change
  while (snapshotsEqual(current, baseline)) {
    checkAbort();
    await sleep(cfg.pollInterval * 1000);
    checkAbort();
    current = fileSnapshot(repoRoot);
  }

  // Phase 2: wait for quiet
  let lastChangeTs = performance.now();
  while (true) {
    checkAbort();
    await sleep(cfg.pollInterval * 1000);
    checkAbort();
    const newSnap = fileSnapshot(repoRoot);
    if (!snapshotsEqual(newSnap, current)) {
      current = newSnap;
      lastChangeTs = performance.now();
      continue;
    }
    if (performance.now() - lastChangeTs >= cfg.debounceSeconds * 1000) {
      return;
    }
  }
}

// ============================================================================
// Self-healing Stitch watch lock
// ============================================================================

export const HEARTBEAT_INTERVAL_MS = 5_000;
export const HEARTBEAT_STALE_MS = 30_000;
export const TERM_GRACE_MS = 2_000;
export const KILL_GRACE_MS = 1_000;
export const LEGACY_STALE_AGE_MS = 600_000;
export const LOCKFILE_NAME = ".stitch.lock";
export const LOCKFILE_TMP = ".stitch.lock.tmp";
const STITCH_CMDLINE_RE =
  /(?:^|[\/\s])(?:node|bun)\b.*\bstitch\b|(?:^|[\/\s])stitch(?:\s|$)/;

export class LockAcquireError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "LockAcquireError";
  }
}

export interface LockfileV1 {
  version: 1;
  pid: number;
  cmdline: string;
  startedAt: number;
  heartbeatAt: number;
}

export type ParsedLockfile =
  | { kind: "v1"; data: LockfileV1 }
  | { kind: "legacy"; pid: number; mtime: number }
  | { kind: "malformed" }
  | { kind: "empty" };

export type PidChecker = (pid: number) => boolean;
export type CmdlineProbe = (pid: number) => string | null;
export type Signaler = (pid: number, signal: NodeJS.Signals | 0) => void;

export interface StitchLockOptions {
  now?: () => number;
  pidAlive?: PidChecker;
  probeCmdline?: CmdlineProbe;
  signal?: Signaler;
  heartbeatIntervalMs?: number;
  heartbeatStaleMs?: number;
  termGraceMs?: number;
  killGraceMs?: number;
  legacyStaleAgeMs?: number;
  registerSignals?: boolean;
}

export function pidAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    const code = (err as NodeJS.ErrnoException).code;
    if (code === "ESRCH") return false;
    // EPERM => process exists but we can't signal it. The decision layer
    // still must verify cmdline before treating this as a live peer.
    if (code === "EPERM") return true;
    return false;
  }
}

export function probeCmdline(pid: number): string | null {
  try {
    const raw = readFileSync(`/proc/${pid}/cmdline`, "utf-8");
    const joined = raw.replace(/\0+$/, "").replace(/\0/g, " ").trim();
    if (joined.length > 0) return joined;
  } catch {
    // fall through to ps
  }
  try {
    const out = execFileSync("ps", ["-p", String(pid), "-o", "command="], {
      encoding: "utf-8",
      stdio: ["ignore", "pipe", "ignore"],
    });
    const trimmed = out.trim();
    if (trimmed.length > 0) return trimmed;
  } catch {
    // ignore
  }
  return null;
}

function defaultSignaler(pid: number, sig: NodeJS.Signals | 0): void {
  try {
    process.kill(pid, sig);
  } catch {
    // swallow; caller interprets via pidAlive polling
  }
}

function sleepSyncMs(ms: number): void {
  if (ms <= 0) return;
  const buf = new SharedArrayBuffer(4);
  const view = new Int32Array(buf);
  Atomics.wait(view, 0, 0, ms);
}

export function parseLockfile(path: string): ParsedLockfile {
  const raw = readFileSync(path, "utf-8");
  if (raw.length === 0) return { kind: "empty" };
  const trimmed = raw.trim();
  if (trimmed.length === 0) return { kind: "empty" };
  // Try JSON v1 first.
  if (trimmed.startsWith("{")) {
    try {
      const obj = JSON.parse(trimmed);
      if (
        obj &&
        typeof obj === "object" &&
        obj.version === 1 &&
        typeof obj.pid === "number" &&
        typeof obj.cmdline === "string" &&
        typeof obj.startedAt === "number" &&
        typeof obj.heartbeatAt === "number"
      ) {
        return { kind: "v1", data: obj as LockfileV1 };
      }
    } catch {
      // fall through
    }
    return { kind: "malformed" };
  }
  // Legacy plain integer.
  const n = Number.parseInt(trimmed, 10);
  if (Number.isFinite(n) && String(n) === trimmed) {
    let mtime = 0;
    try {
      mtime = statSync(path).mtimeMs;
    } catch {
      // best effort; treat as very old
    }
    return { kind: "legacy", pid: n, mtime };
  }
  return { kind: "malformed" };
}

interface DecideDeps {
  now: () => number;
  pidAlive: PidChecker;
  probeCmdline: CmdlineProbe;
  heartbeatStaleMs: number;
  legacyStaleAgeMs: number;
}

export function decide(
  parsed: ParsedLockfile,
  deps: DecideDeps,
): "reclaim" | "terminate" | "block" {
  if (parsed.kind === "malformed" || parsed.kind === "empty") return "reclaim";

  if (parsed.kind === "legacy") {
    if (!deps.pidAlive(parsed.pid)) return "reclaim";
    const cmd = deps.probeCmdline(parsed.pid);
    if (cmd !== null && !STITCH_CMDLINE_RE.test(cmd)) return "reclaim";
    if (deps.now() - parsed.mtime > deps.legacyStaleAgeMs) return "reclaim";
    return "block";
  }

  // v1
  const d = parsed.data;
  if (!deps.pidAlive(d.pid)) return "reclaim";
  const cmd = deps.probeCmdline(d.pid);
  if (cmd !== null) {
    if (!STITCH_CMDLINE_RE.test(cmd) && cmd !== d.cmdline) return "reclaim";
  }
  if (deps.now() - d.heartbeatAt > deps.heartbeatStaleMs) return "terminate";
  return "block";
}

interface TerminateDeps {
  pidAlive: PidChecker;
  signal: Signaler;
  termGraceMs: number;
  killGraceMs: number;
}

export function terminateHung(pid: number, deps: TerminateDeps): void {
  deps.signal(pid, "SIGTERM");
  let waited = 0;
  while (waited < deps.termGraceMs) {
    if (!deps.pidAlive(pid)) return;
    sleepSyncMs(200);
    waited += 200;
  }
  deps.signal(pid, "SIGKILL");
  waited = 0;
  while (waited < deps.killGraceMs) {
    if (!deps.pidAlive(pid)) return;
    sleepSyncMs(200);
    waited += 200;
  }
  throw new LockAcquireError(buildTerminationFailedMessage(pid));
}

export function buildBlockMessage(pid: number): string {
  return `Another Stitch watch is already running (pid ${pid}).`;
}

export function buildTerminationFailedMessage(pid: number): string {
  return `Could not terminate hung Stitch (pid ${pid}) after SIGTERM+SIGKILL. Check process ownership and system state.`;
}

function safeUnlink(path: string): void {
  try {
    unlinkSync(path);
  } catch {
    // ignore
  }
}

export class StitchLock {
  private readonly path: string;
  private readonly tmpPath: string;
  private readonly opts: Required<
    Omit<StitchLockOptions, "registerSignals">
  > & { registerSignals: boolean };
  private heartbeatTimer: NodeJS.Timeout | null = null;
  private released = false;
  private lastState: LockfileV1 | null = null;
  private unregisterSignals: (() => void) | null = null;

  constructor(repoRoot: string, opts: StitchLockOptions = {}) {
    this.path = join(repoRoot, LOCKFILE_NAME);
    this.tmpPath = join(repoRoot, LOCKFILE_TMP);
    this.opts = {
      now: opts.now ?? Date.now,
      pidAlive: opts.pidAlive ?? pidAlive,
      probeCmdline: opts.probeCmdline ?? probeCmdline,
      signal: opts.signal ?? defaultSignaler,
      heartbeatIntervalMs: opts.heartbeatIntervalMs ?? HEARTBEAT_INTERVAL_MS,
      heartbeatStaleMs: opts.heartbeatStaleMs ?? HEARTBEAT_STALE_MS,
      termGraceMs: opts.termGraceMs ?? TERM_GRACE_MS,
      killGraceMs: opts.killGraceMs ?? KILL_GRACE_MS,
      legacyStaleAgeMs: opts.legacyStaleAgeMs ?? LEGACY_STALE_AGE_MS,
      registerSignals: opts.registerSignals ?? true,
    };
  }

  acquire(): void {
    const maxIterations = 16;
    for (let i = 0; i < maxIterations; i++) {
      try {
        this.writeFresh();
        this.released = false;
        this.startHeartbeat();
        if (this.opts.registerSignals) {
          this.unregisterSignals = registerRelease(() => this.release());
        }
        return;
      } catch (err) {
        const code = (err as NodeJS.ErrnoException).code;
        if (code !== "EEXIST") throw err;
      }

      let parsed: ParsedLockfile;
      try {
        parsed = parseLockfile(this.path);
      } catch {
        // File vanished between EEXIST and read; loop and try to create again.
        continue;
      }

      const decision = decide(parsed, {
        now: this.opts.now,
        pidAlive: this.opts.pidAlive,
        probeCmdline: this.opts.probeCmdline,
        heartbeatStaleMs: this.opts.heartbeatStaleMs,
        legacyStaleAgeMs: this.opts.legacyStaleAgeMs,
      });

      if (decision === "reclaim") {
        safeUnlink(this.path);
        continue;
      }
      if (decision === "terminate") {
        const holderPid = parsed.kind === "v1" ? parsed.data.pid : 0;
        terminateHung(holderPid, {
          pidAlive: this.opts.pidAlive,
          signal: this.opts.signal,
          termGraceMs: this.opts.termGraceMs,
          killGraceMs: this.opts.killGraceMs,
        });
        safeUnlink(this.path);
        continue;
      }
      // block
      const holderPid =
        parsed.kind === "v1"
          ? parsed.data.pid
          : parsed.kind === "legacy"
            ? parsed.pid
            : 0;
      throw new LockAcquireError(buildBlockMessage(holderPid));
    }
    throw new LockAcquireError(
      "Stitch could not acquire the watch lock after repeated attempts.",
    );
  }

  release(): void {
    if (this.released) return;
    this.released = true;
    if (this.heartbeatTimer) {
      clearInterval(this.heartbeatTimer);
      this.heartbeatTimer = null;
    }
    try {
      const parsed = parseLockfile(this.path);
      if (parsed.kind === "v1" && parsed.data.pid === process.pid) {
        safeUnlink(this.path);
      } else if (parsed.kind === "legacy" && parsed.pid === process.pid) {
        safeUnlink(this.path);
      }
    } catch {
      // file gone or unreadable; nothing to do
    }
    if (this.unregisterSignals) {
      try {
        this.unregisterSignals();
      } catch {
        // ignore
      }
      this.unregisterSignals = null;
    }
  }

  private writeFresh(): void {
    const cmdline =
      this.opts.probeCmdline(process.pid) ?? process.argv.join(" ");
    const now = this.opts.now();
    const body: LockfileV1 = {
      version: 1,
      pid: process.pid,
      cmdline,
      startedAt: now,
      heartbeatAt: now,
    };
    writeFileSync(this.path, JSON.stringify(body), { flag: "wx" });
    this.lastState = body;
  }

  private startHeartbeat(): void {
    const tick = () => {
      if (this.released || !this.lastState) return;
      const body: LockfileV1 = {
        ...this.lastState,
        heartbeatAt: this.opts.now(),
      };
      try {
        writeFileSync(this.tmpPath, JSON.stringify(body));
        renameSync(this.tmpPath, this.path);
        this.lastState = body;
      } catch {
        // next tick retries
      }
    };
    this.heartbeatTimer = setInterval(tick, this.opts.heartbeatIntervalMs);
    if (typeof this.heartbeatTimer.unref === "function") {
      this.heartbeatTimer.unref();
    }
  }
}

// Back-compat helper used by existing callers/tests.
export function lockfileExists(repoRoot: string): boolean {
  return existsSync(join(repoRoot, LOCKFILE_NAME));
}
