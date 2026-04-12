// STITCH TUI - pure ANSI renderer, no external UI framework

import type { CIJob, JobResult, RunReport } from "../models.js";
import type { RunnerCallback } from "../runner.js";
import { bold, boldFg, dimText, fg, formatElapsed, line, pad, progressBar } from "./ansi.js";
import { Renderer } from "./renderer.js";
import { Spinner } from "./spinner.js";

// ── Colors ─────────────────────────────────────────────────────────────────

const green = "#34D399";
const red = "#F87171";
const blue = "#82AAFF";
const cyan = "#7AA2F7";
const purple = "#C792EA";
const orange = "#FBBF24";

// ── Types ──────────────────────────────────────────────────────────────────

interface JobState {
  name: string;
  stage: string;
  status: string;
  skipReason: string | null;
  startTime: number | null;
  duration: number | null;
  attempts: number;
  maxAttempts: number;
  errorLog: string;
}

interface FixingState {
  label: string;
  driver: string;
  log: string;
}

type Phase = "welcome" | "running" | "done";

interface AppState {
  jobs: JobState[];
  fixing: FixingState | null;
  startTime: number;
  phase: Phase;
  runCount: number;
  lastReport: {
    passed: number;
    failed: number;
    fixed: string[];
    elapsed: number;
    commitSha: string | null;
    pushed: boolean;
  } | null;
}

// ── State ──────────────────────────────────────────────────────────────────

class TuiState {
  state: AppState;
  onRerun: (() => void) | null = null;
  onQuit: (() => void) | null = null;

  constructor() {
    this.state = {
      jobs: [], fixing: null, startTime: Date.now(),
      phase: "welcome", runCount: 0, lastReport: null,
    };
  }

  initJobs(jobs: CIJob[]) {
    this.state = {
      ...this.state,
      jobs: jobs.map((j) => ({
        name: j.name, stage: j.stage,
        status: j.skipReason ? "skipped" : "pending",
        skipReason: j.skipReason,
        startTime: null, duration: null,
        attempts: 0, maxAttempts: 1, errorLog: "",
      })),
      fixing: null, startTime: Date.now(), phase: "running",
    };
  }

  jobStarted(name: string, attempt: number, maxAttempts: number) {
    this.state = {
      ...this.state,
      jobs: this.state.jobs.map((j) =>
        j.name === name
          ? { ...j, status: "running", startTime: Date.now(), attempts: attempt, maxAttempts }
          : j,
      ),
    };
  }

  jobFinished(name: string, result: JobResult) {
    this.state = {
      ...this.state,
      jobs: this.state.jobs.map((j) =>
        j.name === name ? {
          ...j, status: result.status, attempts: result.attempts,
          duration: j.startTime ? (Date.now() - j.startTime) / 1000 : null,
          errorLog: result.errorLog,
        } : j,
      ),
    };
  }

  driverStarted(name: string, driverName: string) {
    const names = new Set(name.split(",").map((n) => n.trim()));
    this.state = {
      ...this.state,
      fixing: { label: name, driver: driverName, log: "" },
      jobs: this.state.jobs.map((j) => names.has(j.name) ? { ...j, status: "fixing" } : j),
    };
  }

  driverLogUpdate(name: string, log: string) {
    if (this.state.fixing?.label === name) {
      this.state = { ...this.state, fixing: { ...this.state.fixing, log } };
    }
  }

  markDone(report: RunReport, commitSha: string | null, pushed: boolean) {
    const passed = report.jobs.filter((j) => j.status === "passed").length;
    const failed = report.jobs.filter((j) => j.status === "escalated" || j.status === "failed").length;
    const elapsed = (Date.now() - this.state.startTime) / 1000;
    this.state = {
      ...this.state, phase: "done", fixing: null,
      runCount: this.state.runCount + 1,
      lastReport: { passed, failed, fixed: report.fixedJobs, elapsed, commitSha, pushed },
    };
  }
}

// ── Render Functions ───────────────────────────────────────────────────────

const LOGO = [
  " \u2588\u2588\u2588 \u2588\u2588\u2588 \u2588\u2588\u2588 \u2588\u2588\u2588 \u2588\u2588\u2588 \u2588 \u2588",
  " \u2588    \u2588   \u2588   \u2588  \u2588   \u2588 \u2588",
  " \u2588\u2588\u2588  \u2588   \u2588   \u2588  \u2588   \u2588\u2588\u2588",
  "   \u2588  \u2588   \u2588   \u2588  \u2588   \u2588 \u2588",
  " \u2588\u2588\u2588  \u2588  \u2588\u2588\u2588  \u2588  \u2588\u2588\u2588 \u2588 \u2588",
];

function renderWelcome(agent: string, repo: string): string {
  const lines: string[] = [""];
  for (const l of LOGO) lines.push("  " + boldFg(blue, l));
  lines.push("");
  lines.push("  " + dimText("v2.0.0"));
  lines.push("  " + dimText("Run your CI jobs locally. Fix failures with AI."));
  lines.push("");
  lines.push(`  ${dimText("Agent:")} ${boldFg(blue, agent)}    ${dimText("Repo:")} ${boldFg(cyan, repo)}`);
  lines.push("");
  lines.push("  " + dimText("Initializing..."));
  lines.push("");
  return lines.join("\n");
}

function statusLabel(s: string): [string, string] {
  switch (s) {
    case "passed": return [" PASS", green];
    case "escalated": case "failed": return [" FAIL", red];
    case "running": return ["  RUN", blue];
    case "fixing": return ["  FIX", purple];
    case "skipped": return [" SKIP", ""];
    case "pending": return [" WAIT", ""];
    default: return ["   --", ""];
  }
}

function renderJobRow(j: JobState, spinner: Spinner): string {
  const [label, color] = statusLabel(j.status);
  const isActive = j.status === "running" || j.status === "fixing";
  const isSkip = j.status === "skipped";

  let info = "";
  if (isActive && j.maxAttempts > 1) info = `attempt ${j.attempts}/${j.maxAttempts}`;
  else if (j.status === "passed" && j.duration !== null) {
    info = `${j.duration.toFixed(1)}s`;
    if (j.attempts > 1) info += ` (${j.attempts} attempts)`;
  } else if ((j.status === "escalated" || j.status === "failed") && j.duration !== null) {
    info = `${j.duration.toFixed(1)}s (${j.attempts} attempts)`;
  } else if (isSkip) {
    info = j.skipReason?.includes("infra") ? "infra" : "skipped";
  }

  const statusStr = isActive
    ? ` ${fg(color, spinner.frame)}${boldFg(color, label.trim())}`
    : color ? boldFg(color, label) : dimText(label);

  const nameStr = isSkip ? dimText(pad(j.name, 22)) : pad(j.name, 22);
  const stageStr = dimText(pad(j.stage, 14));
  const infoStr = dimText(info);

  return `  ${statusStr}  ${nameStr}  ${stageStr}  ${infoStr}`;
}

function renderFrame(state: AppState, agent: string, repo: string, spinner: Spinner): string {
  if (state.phase === "welcome") return renderWelcome(agent, repo);

  const runnable = state.jobs.filter((j) => j.status !== "skipped");
  const skipped = state.jobs.filter((j) => j.status === "skipped");
  const done = runnable.filter((j) => ["passed", "escalated", "failed"].includes(j.status)).length;
  const running = runnable.filter((j) => ["running", "fixing"].includes(j.status)).length;
  const isRunning = state.phase === "running";
  const isDone = state.phase === "done";
  const allPassed = isDone && state.lastReport?.failed === 0;

  const ms = Date.now() - state.startTime;
  const pct = isDone ? 100 : Math.round(Math.min(
    ((done + running * 0.5) / (runnable.length || 1)) * 100 + Math.min(ms / 1000, 10), 99));
  const barColor = allPassed ? green : isDone ? red : blue;

  const lines: string[] = [];

  // ── Header
  const titleColor = allPassed ? green : isDone ? red : blue;
  lines.push(`  ${boldFg(titleColor, "STITCH")}`);
  lines.push(`  ${dimText("Run your CI jobs locally. Fix failures with AI.")}`);
  let infoLine = `  ${dimText("Agent:")} ${boldFg(blue, agent)}  ${dimText("Repo:")} ${boldFg(cyan, repo)}`;
  if (state.runCount > 0) infoLine += `  ${dimText("Run:")} ${fg(purple, "#" + state.runCount)}`;
  infoLine += `  ${dimText("Jobs:")} ${fg(cyan, String(runnable.length))}`;
  if (skipped.length > 0) infoLine += `  ${dimText("Skipped:")} ${dimText(String(skipped.length))}`;
  lines.push(infoLine);

  // ── Progress
  let pLine = `  ${progressBar(pct, 40, barColor)} ${dimText(`${done}/${runnable.length}`)} ${dimText(`${pct}%`)}`;
  if (isRunning) pLine += `  ${fg(cyan, spinner.frame)} ${fg(cyan, formatElapsed(ms))}`;
  if (isDone && state.lastReport) pLine += `  ${dimText(state.lastReport.elapsed.toFixed(1) + "s")}`;
  lines.push(pLine);
  lines.push("");

  // ── Job table
  lines.push(`  ${line(70)}`);
  lines.push(`  ${boldFg(blue, pad("STATUS", 6))}  ${boldFg(blue, pad("JOB", 22))}  ${boldFg(blue, pad("STAGE", 14))}  ${boldFg(blue, "INFO")}`);
  lines.push(`  ${line(70)}`);
  for (const j of runnable) lines.push(renderJobRow(j, spinner));
  if (skipped.length > 0) {
    lines.push(`  ${line(70)}`);
    for (const j of skipped) lines.push(renderJobRow(j, spinner));
  }
  lines.push(`  ${line(70)}`);

  // ── Driver panel
  if (state.fixing && state.fixing.log) {
    lines.push("");
    lines.push(`  ${fg(purple, spinner.frame)} ${boldFg(purple, state.fixing.label)}  ${dimText("fixing with")} ${boldFg(blue, state.fixing.driver)}`);
    const logLines = state.fixing.log.trim().split("\n").slice(-12);
    for (const l of logLines) {
      const lo = l.toLowerCase();
      const c = ["error", "fail", "assert", "exception"].some((k) => lo.includes(k)) ? red
        : l.startsWith("> ") ? cyan : "";
      lines.push(`    ${c ? fg(c, l) : dimText(l)}`);
    }
  }

  // ── Failed errors
  if (isDone) {
    const failed = state.jobs.filter((j) => (j.status === "escalated" || j.status === "failed") && j.errorLog);
    for (const j of failed) {
      lines.push("");
      lines.push(`  ${boldFg(red, "x " + j.name)}`);
      for (const l of j.errorLog.trim().split("\n").slice(-6)) {
        const c = l.toLowerCase().includes("error") ? red : "";
        lines.push(`    ${c ? fg(c, l) : dimText(l)}`);
      }
    }
  }

  // ── Git commit
  if (isDone && state.lastReport?.commitSha) {
    lines.push("");
    let cLine = `  ${fg(green, "*")} ${dimText("committed")} ${boldFg(orange, state.lastReport.commitSha.slice(0, 8))}`;
    cLine += ` ${dimText("fix(stitch): " + state.lastReport.fixed.join(", "))}`;
    if (state.lastReport.pushed) cLine += ` ${fg(green, "pushed")}`;
    lines.push(cLine);
  }

  // ── Result
  if (isDone && state.lastReport) {
    lines.push("");
    lines.push(allPassed
      ? `  ${boldFg(green, `All ${state.lastReport.passed} jobs passed`)}`
      : `  ${boldFg(red, `${state.lastReport.failed} failed, ${state.lastReport.passed} passed`)}`
    );
  }

  // ── Footer
  lines.push("");
  lines.push(`  ${line(70)}`);
  const statusStr = isRunning ? boldFg(blue, " STITCH running")
    : allPassed ? boldFg(green, " STITCH passed")
    : isDone ? boldFg(red, " STITCH failed")
    : boldFg(blue, " STITCH");
  const cmds = isDone
    ? `${bold("enter")} ${dimText("run again")}  ${bold("q")} ${dimText("quit")}`
    : `${bold("q")} ${dimText("quit")}  ${bold("ctrl+c")} ${dimText("abort")}`;
  // Right-align commands: fill space between status and commands
  const gap = Math.max(2, 70 - 18 - 30);
  lines.push(`  ${statusStr}${" ".repeat(gap)}${cmds}`);

  return lines.join("\n");
}

// ── Public API ─────────────────────────────────────────────────────────────

export class StitchUI {
  private tuiState: TuiState;
  private renderer: Renderer;
  private spinner: Spinner;
  private stdinHandler: ((data: Buffer) => void) | null = null;
  private agent: string;
  private repo: string;

  constructor(agent: string, repo: string) {
    this.tuiState = new TuiState();
    this.renderer = new Renderer();
    this.spinner = new Spinner();
    this.agent = agent;
    this.repo = repo.split("/").slice(-2).join("/");
  }

  initJobs(jobs: CIJob[]) {
    this.tuiState.initJobs(jobs);
    this.renderer.repaint();
  }

  start() {
    this.renderer.enter();
    this.spinner.start(() => this.renderer.repaint());
    this.renderer.startLoop(
      () => renderFrame(this.tuiState.state, this.agent, this.repo, this.spinner),
      200,
    );

    // Keyboard handler
    if (process.stdin.isTTY) {
      process.stdin.setRawMode(true);
      process.stdin.resume();
      this.stdinHandler = (data: Buffer) => {
        const key = data.toString();
        if (key === "q" || key === "Q" || key === "\x03") {
          this.stop();
          if (this.tuiState.onQuit) this.tuiState.onQuit();
          process.exit(0);
        }
        if ((key === "\r" || key === "\n" || key === "r" || key === "R") && this.tuiState.state.phase === "done") {
          this.tuiState.onRerun?.();
        }
      };
      process.stdin.on("data", this.stdinHandler);
    }
  }

  stop() {
    this.spinner.stop();
    this.renderer.stopLoop();
    this.renderer.exit();
    if (this.stdinHandler) {
      process.stdin.removeListener("data", this.stdinHandler);
      this.stdinHandler = null;
      try { if (process.stdin.isTTY) process.stdin.setRawMode(false); } catch { /* */ }
    }
  }

  markDone(report: RunReport, commitSha: string | null, pushed: boolean) {
    this.tuiState.markDone(report, commitSha, pushed);
    this.renderer.repaint();
  }

  waitForRerun(): Promise<"rerun" | "quit"> {
    return new Promise((resolve) => {
      this.tuiState.onRerun = () => resolve("rerun");
      this.tuiState.onQuit = () => resolve("quit");
    });
  }

  get callback(): RunnerCallback {
    return {
      jobStarted: (n, a, m) => { this.tuiState.jobStarted(n, a, m); this.renderer.repaint(); },
      jobLogUpdate: () => {},
      jobFinished: (n, r) => { this.tuiState.jobFinished(n, r); this.renderer.repaint(); },
      driverStarted: (n, d) => { this.tuiState.driverStarted(n, d); this.renderer.repaint(); },
      driverLogUpdate: (n, l) => { this.tuiState.driverLogUpdate(n, l); this.renderer.repaint(); },
    };
  }
}
