import { existsSync } from "node:fs";
import { resolve } from "node:path";
import { type HistoryEntry, type HistoryStatus, readHistory } from "../core/history.js";

export interface HistoryOptions {
  repo: string;
  job?: string;
  limit: number;
  output: string;
}

const cGreen = "52;211;153";
const cRed = "248;113;113";
const cBlue = "130;170;255";
const cCyan = "122;162;247";
const cPurple = "199;146;234";
const cOrange = "251;191;36";

function fg(rgb: string, text: string): string {
  return `\x1b[38;2;${rgb}m${text}\x1b[0m`;
}
function bold(text: string): string {
  return `\x1b[1m${text}\x1b[22m`;
}
function dim(text: string): string {
  return `\x1b[2m${text}\x1b[22m`;
}
function pad(s: string, n: number): string {
  return s.length >= n ? s.slice(0, n) : s + " ".repeat(n - s.length);
}

const STITCH_LOGO = [" █▀▀ ▀█▀ █ ▀█▀ █▀▀ █ █", "  ▄▄█  █  █  █  █▄▄ █▀█"];

interface HeaderStats {
  agent: string | null;
  repo: string;
  streaks: number;
  runs: number;
  jobs: number;
  fixed: number;
  escalated: number;
}

function renderHeader(stats: HeaderStats): string {
  const logo = STITCH_LOGO.map((l) => `  ${fg(cCyan, l)}`).join("\n");
  const parts: string[] = [];
  if (stats.agent) parts.push(`${dim("Agent:")} ${fg(cBlue, bold(stats.agent))}`);
  parts.push(`${dim("Repo:")} ${fg(cCyan, bold(stats.repo))}`);
  parts.push(`${dim("Runs:")} ${fg(cPurple, `#${stats.runs}`)}`);
  parts.push(`${dim("Streaks:")} ${fg(cCyan, String(stats.streaks))}`);
  parts.push(`${dim("Jobs:")} ${fg(cCyan, String(stats.jobs))}`);
  if (stats.fixed > 0) parts.push(`${dim("Fixed:")} ${fg(cPurple, String(stats.fixed))}`);
  if (stats.escalated > 0) parts.push(`${dim("Failed:")} ${fg(cRed, String(stats.escalated))}`);
  const info = parts.join(dim("  \u00b7  "));
  return `\n${logo}\n\n${info}\n`;
}

function repoLabel(repoRoot: string): string {
  const parts = repoRoot.replace(/\/+$/, "").split("/").filter(Boolean);
  return parts.slice(-2).join("/") || repoRoot;
}

function statusIcon(s: HistoryStatus): string {
  switch (s) {
    case "passed":
      return fg(cGreen, "\u2713");
    case "fixed":
      return fg(cPurple, "\u270e");
    case "escalated":
      return fg(cRed, "\u2717");
  }
}

function statusLabel(s: HistoryStatus): string {
  switch (s) {
    case "passed":
      return fg(cGreen, pad("PASS", 5));
    case "fixed":
      return fg(cPurple, pad("FIXED", 5));
    case "escalated":
      return fg(cRed, pad("FAIL", 5));
  }
}

function formatRelative(iso: string, refMs: number): string {
  const ms = refMs - new Date(iso).getTime();
  if (ms < 60_000) return `${Math.floor(ms / 1000)}s ago`;
  if (ms < 3_600_000) return `${Math.floor(ms / 60_000)}m ago`;
  if (ms < 86_400_000) return `${Math.floor(ms / 3_600_000)}h ago`;
  return `${Math.floor(ms / 86_400_000)}d ago`;
}

function renderRow(e: HistoryEntry, ongoing: boolean, refMs: number): string {
  const when = formatRelative(e.lastAt, refMs);
  const runs = e.runs > 1 ? `\u00d7${e.runs}` : "";
  const trailing: string[] = [];
  if (e.attempts > 1) trailing.push(`${e.attempts} attempts`);
  if (e.commitSha) trailing.push(e.commitSha.slice(0, 8));
  if (e.errorFirstLine) trailing.push(e.errorFirstLine);
  if (ongoing) trailing.push(dim("ongoing"));
  const trail = trailing.length > 0 ? `  ${dim(trailing.join("  "))}` : "";
  return `  ${statusIcon(e.status)} ${statusLabel(e.status)}  ${bold(pad(e.job, 22))}${dim(pad(when, 10))}${dim(pad(runs, 6))}${trail}`;
}

export async function runHistoryCommand(opts: HistoryOptions): Promise<number> {
  const repoRoot = resolve(opts.repo);
  if (!existsSync(repoRoot)) {
    console.error(`Error: repo path not found: ${repoRoot}`);
    return 2;
  }

  const view = readHistory(repoRoot, { job: opts.job, limit: opts.limit });

  if (opts.output === "json") {
    process.stdout.write(`${JSON.stringify(view, null, 2)}\n`);
    return 0;
  }

  const all = [...view.finalized, ...view.ongoing];
  const fixed = all.filter((e) => e.status === "fixed").length;
  const failed = all.filter((e) => e.status === "escalated").length;
  const passed = all.filter((e) => e.status === "passed").length;
  const passedRuns = all
    .filter((e) => e.status === "passed")
    .reduce((acc, e) => acc + e.runs, 0);
  const totalRuns = all.reduce((acc, e) => acc + e.runs, 0);
  const distinctJobs = new Set(all.map((e) => e.job)).size;
  const latestAgent =
    [...view.ongoing, ...view.finalized]
      .map((e) => e.agent)
      .find((a): a is string => Boolean(a)) ?? null;

  const stats: HeaderStats = {
    agent: latestAgent,
    repo: repoLabel(repoRoot),
    streaks: all.length,
    runs: totalRuns,
    jobs: distinctJobs,
    fixed,
    escalated: failed,
  };

  if (view.finalized.length === 0 && view.ongoing.length === 0) {
    process.stdout.write(
      `${renderHeader(stats)}\n  ${dim("No runs recorded yet. Run `stitch run` first.")}\n\n`,
    );
    return 0;
  }

  const refMs = Date.now();
  const line = dim("\u2500".repeat(72));

  process.stdout.write(`${renderHeader(stats)}\n  ${line}\n`);
  for (const e of view.finalized) {
    process.stdout.write(`${renderRow(e, false, refMs)}\n`);
  }
  if (view.ongoing.length > 0) {
    if (view.finalized.length > 0) process.stdout.write(`  ${line}\n`);
    for (const e of view.ongoing) {
      process.stdout.write(`${renderRow(e, true, refMs)}\n`);
    }
  }
  process.stdout.write(`  ${line}\n\n`);

  const summary = [
    `${fg(cGreen, bold(String(passed)))} passed streaks`,
    `${fg(cGreen, dim(`(${passedRuns} runs)`))}`,
    `${fg(cPurple, bold(String(fixed)))} fixed`,
    `${fg(cOrange, bold(String(failed)))} escalated`,
  ].join(dim("  \u00b7  "));

  process.stdout.write(`  ${summary}\n\n`);
  return 0;
}
