import {
  appendFileSync,
  existsSync,
  mkdirSync,
  readFileSync,
  renameSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { dirname, join } from "node:path";
import type { GitSnapshot, JobResult, RunReport } from "./models.js";

export const HISTORY_SCHEMA_VERSION = 1;
export const ROTATE_LINE_THRESHOLD = 5_000;
const HISTORY_DIR = ".stitch";
const HISTORY_FILE = "history.jsonl";
const HISTORY_BACKUP = "history.1.jsonl";
const HEAD_FILE = "history-head.json";

export type HistoryStatus = "passed" | "fixed" | "escalated";

export interface HistoryEntry {
  v: number;
  job: string;
  status: HistoryStatus;
  agent: string | null;
  attempts: number;
  errorFirstLine: string | null;
  branch: string | null;
  commitSha: string | null;
  runs: number;
  firstAt: string;
  lastAt: string;
}

interface HeadFile {
  v: number;
  jobs: Record<string, HistoryEntry>;
}

function historyPaths(repoRoot: string) {
  const dir = join(repoRoot, HISTORY_DIR);
  return {
    dir,
    log: join(dir, HISTORY_FILE),
    backup: join(dir, HISTORY_BACKUP),
    head: join(dir, HEAD_FILE),
  };
}

function ensureDir(path: string) {
  if (!existsSync(path)) mkdirSync(path, { recursive: true });
}

function readHead(headPath: string): HeadFile {
  if (!existsSync(headPath)) return { v: HISTORY_SCHEMA_VERSION, jobs: {} };
  try {
    const parsed = JSON.parse(readFileSync(headPath, "utf-8"));
    if (parsed && typeof parsed === "object" && parsed.v === HISTORY_SCHEMA_VERSION) {
      return parsed as HeadFile;
    }
  } catch {
    // Corrupt head: start over rather than crash a run
  }
  return { v: HISTORY_SCHEMA_VERSION, jobs: {} };
}

function writeHead(headPath: string, head: HeadFile) {
  ensureDir(dirname(headPath));
  writeFileSync(headPath, `${JSON.stringify(head, null, 2)}\n`);
}

function appendEntry(logPath: string, backupPath: string, entry: HistoryEntry) {
  ensureDir(dirname(logPath));
  appendFileSync(logPath, `${JSON.stringify(entry)}\n`);
  rotateIfNeeded(logPath, backupPath);
}

function rotateIfNeeded(logPath: string, backupPath: string) {
  if (!existsSync(logPath)) return;
  const lines = countLines(logPath);
  if (lines < ROTATE_LINE_THRESHOLD) return;
  // Replace any existing backup; only keep one rotation file.
  if (existsSync(backupPath)) {
    try {
      writeFileSync(backupPath, "");
    } catch {
      // ignore
    }
  }
  renameSync(logPath, backupPath);
}

function countLines(path: string): number {
  try {
    const size = statSync(path).size;
    if (size === 0) return 0;
    return readFileSync(path, "utf-8").split("\n").filter(Boolean).length;
  } catch {
    return 0;
  }
}

function firstLine(text: string): string | null {
  const t = text.trim();
  if (!t) return null;
  return t.split("\n", 1)[0]!.slice(0, 200);
}

function classify(result: JobResult): HistoryStatus | null {
  if (result.status === "passed") return result.attempts > 1 ? "fixed" : "passed";
  if (result.status === "escalated" || result.status === "failed") return "escalated";
  return null; // skipped, not_run -> not recorded
}

function entryFromResult(
  result: JobResult,
  status: HistoryStatus,
  agent: string,
  branch: string | null,
  commitSha: string | null,
  now: string,
): HistoryEntry {
  return {
    v: HISTORY_SCHEMA_VERSION,
    job: result.name,
    status,
    agent,
    attempts: result.attempts,
    errorFirstLine: status === "escalated" ? firstLine(result.errorLog) : null,
    branch,
    commitSha: status === "fixed" ? commitSha : null,
    runs: 1,
    firstAt: now,
    lastAt: now,
  };
}

function streakMatches(prev: HistoryEntry, next: HistoryEntry): boolean {
  return (
    prev.status === next.status &&
    prev.attempts === next.attempts &&
    prev.errorFirstLine === next.errorFirstLine
  );
}

export interface RecordContext {
  repoRoot: string;
  agent: string;
  snap: GitSnapshot;
  commitSha: string | null;
  now?: () => Date;
}

export function recordRun(report: RunReport, ctx: RecordContext): void {
  const paths = historyPaths(ctx.repoRoot);
  const head = readHead(paths.head);
  const nowIso = (ctx.now ? ctx.now() : new Date()).toISOString();

  for (const result of report.jobs) {
    const status = classify(result);
    if (!status) continue;

    const next = entryFromResult(result, status, ctx.agent, ctx.snap.branch, ctx.commitSha, nowIso);
    const prev = head.jobs[result.name];

    if (prev && streakMatches(prev, next)) {
      prev.runs += 1;
      prev.lastAt = nowIso;
      // commitSha may have appeared on a later run of the same streak; preserve latest
      if (next.commitSha) prev.commitSha = next.commitSha;
    } else {
      if (prev) appendEntry(paths.log, paths.backup, prev);
      head.jobs[result.name] = next;
    }
  }

  writeHead(paths.head, head);
}

export interface ReadOptions {
  job?: string;
  limit?: number;
}

export interface HistoryView {
  finalized: HistoryEntry[];
  ongoing: HistoryEntry[];
}

export function readHistory(repoRoot: string, opts: ReadOptions = {}): HistoryView {
  const paths = historyPaths(repoRoot);
  const head = readHead(paths.head);

  const finalized: HistoryEntry[] = [];
  for (const file of [paths.backup, paths.log]) {
    if (!existsSync(file)) continue;
    for (const line of readFileSync(file, "utf-8").split("\n")) {
      if (!line.trim()) continue;
      try {
        const parsed = JSON.parse(line) as HistoryEntry;
        if (parsed.v === HISTORY_SCHEMA_VERSION) finalized.push(parsed);
      } catch {
        // Skip corrupt lines silently
      }
    }
  }

  let ongoing = Object.values(head.jobs);

  let filtered = finalized;
  if (opts.job) {
    filtered = filtered.filter((e) => e.job === opts.job);
    ongoing = ongoing.filter((e) => e.job === opts.job);
  }

  filtered.sort((a, b) => a.lastAt.localeCompare(b.lastAt));
  if (opts.limit && opts.limit > 0 && filtered.length > opts.limit) {
    filtered = filtered.slice(-opts.limit);
  }

  ongoing.sort((a, b) => a.job.localeCompare(b.job));

  return { finalized: filtered, ongoing };
}
