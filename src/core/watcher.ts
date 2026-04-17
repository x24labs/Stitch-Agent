import {
  existsSync,
  readFileSync,
  readdirSync,
  statSync,
  unlinkSync,
  writeFileSync,
} from "node:fs";
import { join, relative } from "node:path";

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

const IGNORE_FILES = new Set([".stitch.lock", ".DS_Store", "Thumbs.db"]);

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

export class LockAcquireError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "LockAcquireError";
  }
}

export class StitchLock {
  private path: string;

  constructor(repoRoot: string) {
    this.path = join(repoRoot, ".stitch.lock");
  }

  acquire(): void {
    if (existsSync(this.path)) {
      const otherPid = this.readPid();
      if (otherPid !== null && pidAlive(otherPid)) {
        throw new LockAcquireError(
          `Another Stitch instance is running (pid ${otherPid}). ` +
            `If this is wrong, delete ${this.path} manually.`,
        );
      }
      try {
        unlinkSync(this.path);
      } catch {
        // ignore
      }
    }
    writeFileSync(this.path, String(process.pid));
  }

  release(): void {
    try {
      const current = this.readPid();
      if (current === process.pid) {
        unlinkSync(this.path);
      }
    } catch {
      // ignore
    }
  }

  private readPid(): number | null {
    try {
      const raw = readFileSync(this.path, "utf-8").trim();
      const pid = Number.parseInt(raw, 10);
      return Number.isNaN(pid) ? null : pid;
    } catch {
      return null;
    }
  }
}

function pidAlive(pid: number): boolean {
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code === "ESRCH") return false;
    if ((err as NodeJS.ErrnoException).code === "EPERM") return true;
    return false;
  }
}
