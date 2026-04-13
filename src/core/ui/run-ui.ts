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

const PIPELINE_STEPS = ["Detect", "Parse", "Classify", "Execute", "Fix", "Commit", "Done"];

class TuiState {
  state: AppState;
  loadingMsg = "Detecting CI configuration...";
  pipelineStep = 0; // index into PIPELINE_STEPS
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
    this.pipelineStep = 3; // Execute
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
    this.pipelineStep = 4; // Fix
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
    if (commitSha) this.pipelineStep = 5; // Commit
    const passed = report.jobs.filter((j) => j.status === "passed").length;
    const failed = report.jobs.filter((j) => j.status === "escalated" || j.status === "failed").length;
    const elapsed = (Date.now() - this.state.startTime) / 1000;
    this.pipelineStep = 6; // Done
    this.state = {
      ...this.state, phase: "done", fixing: null,
      runCount: this.state.runCount + 1,
      lastReport: { passed, failed, fixed: report.fixedJobs, elapsed, commitSha, pushed },
    };
  }
}

// ── Pipeline Stepper ───────────────────────────────────────────────────────

function renderPipeline(step: number, cols: number): string {
  const isLast = step >= PIPELINE_STEPS.length - 1;
  const parts: string[] = [];
  for (let i = 0; i < PIPELINE_STEPS.length; i++) {
    const name = PIPELINE_STEPS[i]!;
    if (i < step || (i === step && isLast)) {
      // Completed: green
      parts.push(boldFg(green, `[ ${name} ]`));
    } else if (i === step) {
      // Current: cyan
      parts.push(boldFg(cyan, `[ ${name} ]`));
    } else {
      // Pending: dim
      parts.push(dimText(`[ ${name} ]`));
    }
    if (i < PIPELINE_STEPS.length - 1) {
      parts.push(i < step || (i < step + 1 && isLast) ? fg(green, " \u2192 ") : dimText(" \u2192 "));
    }
  }
  const line = parts.join("");
  return center(line, cols);
}

// ── Render Functions ───────────────────────────────────────────────────────

// Big block font: each letter is 5 rows x 4 cols (3 visible + 1 space)
const BLOCK_FONT: Record<string, string[]> = {
  S: ["\u2588\u2588\u2588", "\u2588  ", "\u2588\u2588\u2588", "  \u2588", "\u2588\u2588\u2588"],
  T: ["\u2588\u2588\u2588", " \u2588 ", " \u2588 ", " \u2588 ", " \u2588 "],
  I: ["\u2588\u2588\u2588", " \u2588 ", " \u2588 ", " \u2588 ", "\u2588\u2588\u2588"],
  C: ["\u2588\u2588\u2588", "\u2588  ", "\u2588  ", "\u2588  ", "\u2588\u2588\u2588"],
  H: ["\u2588 \u2588", "\u2588 \u2588", "\u2588\u2588\u2588", "\u2588 \u2588", "\u2588 \u2588"],
};

function bigText(word: string): string[] {
  const rows: string[] = [];
  for (let r = 0; r < 5; r++) {
    let line = "";
    for (const ch of word) {
      const glyph = BLOCK_FONT[ch];
      if (glyph) {
        if (line.length > 0) line += "  ";
        line += glyph[r]!;
      }
    }
    rows.push(line);
  }
  return rows;
}

function center(text: string, width: number): string {
  const visible = text.replace(/\x1b\[[0-9;]*m/g, "").length;
  const left = Math.max(0, Math.floor((width - visible) / 2));
  return " ".repeat(left) + text;
}

function renderWelcome(agent: string, repo: string, spinner: Spinner, loadingMsg: string, pipelineStep: number): string {
  const cols = process.stdout.columns || 80;
  const rows = process.stdout.rows || 24;
  const logo = bigText("STITCH");

  const content: string[] = [];

  // Logo
  for (const l of logo) content.push(center(bold(l), cols));
  content.push("");

  // Tagline
  content.push(center(bold("Run your CI jobs locally. Fix failures with AI."), cols));
  content.push("");

  // Stats line
  const stats = `${boldFg(blue, "Agent")} ${dimText(agent)}   ${fg(cyan, "\u00b7")}   ${boldFg(cyan, "Repo")} ${dimText(repo)}   ${fg(cyan, "\u00b7")}   ${boldFg(purple, "v2.0.0")}`;
  content.push(center(stats, cols));
  content.push("");
  content.push("");

  // Pipeline stepper
  content.push(renderPipeline(pipelineStep, cols));
  content.push("");

  // Loading state with spinner
  const loadLine = `${fg(cyan, spinner.frame)}  ${dimText(loadingMsg)}`;
  content.push(center(loadLine, cols));

  // Vertically center: add blank lines above
  const totalLines = content.length;
  const topPad = Math.max(0, Math.floor((rows - totalLines - 3) / 2));
  const lines: string[] = [];
  for (let i = 0; i < topPad; i++) lines.push("");
  lines.push(...content);

  // Fill remaining space
  const remaining = Math.max(0, rows - lines.length - 2);
  for (let i = 0; i < remaining; i++) lines.push("");

  // Footer at bottom
  lines.push(dimText(line(cols - 2)));
  const footerLeft = `  ${boldFg(blue, "\u2588\u2590\u2588")} ${bold("STITCH")}`;
  const footerRight = `${dimText("q to exit")}  `;
  const footerGap = Math.max(2, cols - 20 - 12);
  lines.push(`${footerLeft}${" ".repeat(footerGap)}${footerRight}`);

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

  const isPassed = j.status === "passed";
  const isFailed = j.status === "escalated" || j.status === "failed";
  const statusStr = isActive
    ? ` ${fg(color, spinner.frame)} ${boldFg(color, label.trim())}`
    : isPassed ? `${fg(green, " \u2713")}${boldFg(green, label)}`
    : isFailed ? `${fg(red, " \u2717")}${boldFg(red, label)}`
    : isSkip ? dimText(` \u00bb${label}`)
    : color ? boldFg(color, label) : dimText(label);

  const nameStr = isSkip ? dimText(pad(j.name, 22)) : pad(j.name, 22);
  const stageStr = dimText(pad(j.stage, 14));
  const infoStr = dimText(info);

  return `  ${statusStr}  ${nameStr}  ${stageStr}  ${infoStr}`;
}

function renderFrame(tuiState: TuiState, agent: string, repo: string, spinner: Spinner): string {
  const state = tuiState.state;
  if (state.phase === "welcome") return renderWelcome(agent, repo, spinner, tuiState.loadingMsg, tuiState.pipelineStep);

  const cols = process.stdout.columns || 80;
  const w = cols - 4; // content width (2 padding each side)
  const barW = Math.max(20, Math.min(w - 25, 60)); // progress bar width

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
  const logo = bigText("STITCH");
  for (const l of logo) lines.push(`  ${bold(l)}`);
  lines.push(`  ${"Run your CI jobs locally. Fix failures with AI."}`);
  lines.push("");
  let infoLine = `  ${dimText("Agent:")} ${boldFg(blue, agent)}  ${dimText("Repo:")} ${boldFg(cyan, repo)}`;
  if (state.runCount > 0) infoLine += `  ${dimText("Run:")} ${fg(purple, "#" + state.runCount)}`;
  infoLine += `  ${dimText("Jobs:")} ${fg(cyan, String(runnable.length))}`;
  if (skipped.length > 0) infoLine += `  ${dimText("Skipped:")} ${dimText(String(skipped.length))}`;
  lines.push(infoLine);
  lines.push("");

  // ── Progress
  let pLine = `  ${progressBar(pct, barW, barColor)} ${dimText(`${done}/${runnable.length}`)} ${dimText(`${pct}%`)}`;
  if (isRunning) pLine += `  ${fg(cyan, spinner.frame)} ${fg(cyan, formatElapsed(ms))}`;
  if (isDone && state.lastReport) pLine += `  ${dimText(state.lastReport.elapsed.toFixed(1) + "s")}`;
  lines.push(pLine);
  lines.push("");

  // ── Job table
  lines.push(`  ${line(w)}`);
  lines.push(`  ${boldFg(blue, pad("STATUS", 6))}  ${boldFg(blue, pad("JOB", 22))}  ${boldFg(blue, pad("STAGE", 14))}  ${boldFg(blue, "INFO")}`);
  lines.push(`  ${line(w)}`);
  for (const j of runnable) lines.push(renderJobRow(j, spinner));
  if (skipped.length > 0) {
    lines.push(`  ${line(w)}`);
    for (const j of skipped) lines.push(renderJobRow(j, spinner));
  }
  lines.push(`  ${line(w)}`);

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

  // ── Pipeline
  lines.push("");
  lines.push(renderPipeline(tuiState.pipelineStep, cols));

  // ── Footer
  lines.push("");
  lines.push(`  ${line(w)}`);
  let statusStr: string;
  let statusVisible: number;
  if (allPassed && state.lastReport) {
    statusStr = boldFg(green, ` STITCH - All ${state.lastReport.passed} jobs passed`);
    statusVisible = 28 + String(state.lastReport.passed).length;
  } else if (isDone && state.lastReport) {
    statusStr = boldFg(red, ` STITCH - ${state.lastReport.failed} failed, ${state.lastReport.passed} passed`);
    statusVisible = 22 + String(state.lastReport.failed).length + String(state.lastReport.passed).length;
  } else if (isRunning) {
    statusStr = boldFg(blue, " STITCH - Running");
    statusVisible = 18;
  } else {
    statusStr = boldFg(blue, " STITCH");
    statusVisible = 8;
  }
  const cmdsStr = isDone
    ? `${bold("enter")} ${dimText("run again")}  ${bold("q")} ${dimText("quit")}`
    : `${bold("q")} ${dimText("quit")}  ${bold("ctrl+c")} ${dimText("abort")}`;
  const cmdsVisible = isDone ? 23 : 22;
  const gap = Math.max(2, cols - 2 - statusVisible - cmdsVisible - 2);
  lines.push(`  ${statusStr}${" ".repeat(gap)}${cmdsStr}`);

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
  startedAt = 0;

  constructor(agent: string, repo: string) {
    this.tuiState = new TuiState();
    this.renderer = new Renderer();
    this.spinner = new Spinner();
    this.agent = agent;
    this.repo = repo.split("/").slice(-2).join("/");
  }

  setLoading(msg: string, step?: number) {
    this.tuiState.loadingMsg = msg;
    if (step !== undefined) this.tuiState.pipelineStep = step;
    this.renderer.repaint();
  }

  initJobs(jobs: CIJob[]) {
    this.tuiState.initJobs(jobs);
    this.renderer.repaint();
  }

  start() {
    this.startedAt = Date.now();
    this.renderer.enter();
    this.spinner.start(() => this.renderer.repaint());
    this.renderer.startLoop(
      () => renderFrame(this.tuiState, this.agent, this.repo, this.spinner),
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
