// STITCH TUI - OpenTUI-backed renderer (flicker-free)

import type { CliRenderer } from "@opentui/core";
import {
  ASCIIFontRenderable,
  BoxRenderable,
  StyledText,
  TextRenderable,
  bold,
  dim,
  fg,
  t,
} from "@opentui/core";
import { VERSION } from "../../version.js";
import type { CIJob, CommitPushReason, JobResult, RunReport } from "../models.js";
import type { RunnerCallback } from "../runner.js";
import { createRenderer } from "./renderer.js";

// ── Colors ─────────────────────────────────────────────────────────────────

const cGreen = "#34D399";
const cRed = "#F87171";
const cBlue = "#82AAFF";
const cCyan = "#7AA2F7";
const cPurple = "#C792EA";
const cOrange = "#FBBF24";

// ── Spinner ────────────────────────────────────────────────────────────────

const SPINNER_FRAMES = [
  "\u280B",
  "\u2819",
  "\u2839",
  "\u2838",
  "\u283C",
  "\u2834",
  "\u2826",
  "\u2827",
  "\u2807",
  "\u280F",
];

class Spinner {
  private idx = 0;
  private timer: ReturnType<typeof setInterval> | null = null;

  get frame(): string {
    return SPINNER_FRAMES[this.idx % SPINNER_FRAMES.length] ?? "";
  }

  start(onTick: () => void): void {
    if (this.timer) return;
    this.timer = setInterval(() => {
      this.idx = (this.idx + 1) % SPINNER_FRAMES.length;
      onTick();
    }, 80);
  }

  stop(): void {
    if (this.timer) {
      clearInterval(this.timer);
      this.timer = null;
    }
  }
}

// ── State ──────────────────────────────────────────────────────────────────

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
    reason?: CommitPushReason;
  } | null;
}

const PIPELINE_STEPS = ["Detect", "Parse", "Classify", "Execute", "Fix", "Commit", "Done"];

class TuiState {
  state: AppState;
  loadingMsg = "Detecting CI configuration...";
  notice = "";
  pipelineStep = 0;
  onRerun: (() => void) | null = null;
  onQuit: (() => void) | null = null;
  onAbort: (() => void) | null = null;

  constructor() {
    this.state = {
      jobs: [],
      fixing: null,
      startTime: Date.now(),
      phase: "welcome",
      runCount: 0,
      lastReport: null,
    };
  }

  initJobs(jobs: CIJob[]) {
    this.state = {
      ...this.state,
      jobs: jobs.map((j) => ({
        name: j.name,
        stage: j.stage,
        status: j.skipReason ? "skipped" : "pending",
        skipReason: j.skipReason,
        startTime: null,
        duration: null,
        attempts: 0,
        maxAttempts: 1,
        errorLog: "",
      })),
      fixing: null,
      startTime: Date.now(),
      phase: "running",
    };
    this.pipelineStep = 3;
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
        j.name === name
          ? {
              ...j,
              status: result.status,
              attempts: result.attempts,
              duration: j.startTime ? (Date.now() - j.startTime) / 1000 : null,
              errorLog: result.errorLog,
            }
          : j,
      ),
    };
  }

  driverStarted(name: string, driverName: string) {
    this.pipelineStep = 4;
    const names = new Set(name.split(",").map((n) => n.trim()));
    this.state = {
      ...this.state,
      fixing: { label: name, driver: driverName, log: "" },
      jobs: this.state.jobs.map((j) => (names.has(j.name) ? { ...j, status: "fixing" } : j)),
    };
  }

  driverLogUpdate(name: string, log: string) {
    if (this.state.fixing?.label === name) {
      this.state = { ...this.state, fixing: { ...this.state.fixing, log } };
    }
  }

  markDone(
    report: RunReport,
    commitSha: string | null,
    pushed: boolean,
    reason?: CommitPushReason,
  ) {
    if (commitSha) this.pipelineStep = 5;
    const passed = report.jobs.filter((j) => j.status === "passed").length;
    const failed = report.jobs.filter(
      (j) => j.status === "escalated" || j.status === "failed",
    ).length;
    const elapsed = (Date.now() - this.state.startTime) / 1000;
    this.pipelineStep = 6;
    this.state = {
      ...this.state,
      phase: "done",
      fixing: null,
      runCount: this.state.runCount + 1,
      lastReport: { passed, failed, fixed: report.fixedJobs, elapsed, commitSha, pushed, reason },
    };
  }
}

// ── Styled Text Helpers ───────────────────────────────────────────────────

function styledLine(width: number): StyledText {
  return t`${dim("\u2500".repeat(width))}`;
}

function styledProgressBar(pct: number, width: number, color: string): StyledText {
  const filled = Math.round((pct / 100) * width);
  const bar = "\u2588".repeat(filled) + "\u2591".repeat(width - filled);
  return t`${fg(color)(bar)}`;
}

function formatElapsed(ms: number): string {
  const s = Math.floor(ms / 1000);
  const m = Math.floor(s / 60);
  const tenth = Math.floor((ms % 1000) / 100);
  return m > 0 ? `${m}:${(s % 60).toString().padStart(2, "0")}.${tenth}` : `${s}.${tenth}s`;
}

function pad(s: string, n: number): string {
  return s.length >= n ? s.slice(0, n) : s + " ".repeat(n - s.length);
}

// ── Pipeline Stepper ───────────────────────────────────────────────────────

function buildPipelineText(step: number): StyledText {
  const isLast = step >= PIPELINE_STEPS.length - 1;
  const parts: ReturnType<typeof bold>[] = [];
  PIPELINE_STEPS.forEach((name, i) => {
    if (i < step || (i === step && isLast)) {
      parts.push(fg(cGreen)(bold(`[ ${name} ]`)));
    } else if (i === step) {
      parts.push(fg(cCyan)(bold(`[ ${name} ]`)));
    } else {
      parts.push(dim(`[ ${name} ]`));
    }
    if (i < PIPELINE_STEPS.length - 1) {
      parts.push(i < step || (i < step + 1 && isLast) ? fg(cGreen)(" \u2192 ") : dim(" \u2192 "));
    }
  });
  return new StyledText(parts);
}

// ── Job Row Builder ───────────────────────────────────────────────────────

function buildJobInfo(j: JobState): string {
  const isActive = j.status === "running" || j.status === "fixing";
  const isPassed = j.status === "passed";
  const isFailed = j.status === "escalated" || j.status === "failed";
  if (isActive && j.maxAttempts > 1) return `attempt ${j.attempts}/${j.maxAttempts}`;
  if (isPassed && j.duration !== null) {
    const base = `${j.duration.toFixed(1)}s`;
    return j.attempts > 1 ? `${base} (${j.attempts} attempts)` : base;
  }
  if (isFailed && j.duration !== null) return `${j.duration.toFixed(1)}s (${j.attempts} attempts)`;
  if (j.status === "skipped") return j.skipReason?.includes("infra") ? "infra" : "skipped";
  return "";
}

function buildStatusChunks(status: string, spinnerFrame: string): ReturnType<typeof bold>[] {
  const [label, color] = statusLabel(status);
  const padded = pad(label.trim(), 6);
  switch (status) {
    case "running":
    case "fixing":
      return [fg(color)(`${spinnerFrame} `), fg(color)(padded)];
    case "passed":
      return [fg(cGreen)("\u2713 "), fg(cGreen)(padded)];
    case "escalated":
    case "failed":
      return [fg(cRed)("\u2717 "), fg(cRed)(padded)];
    case "skipped":
      return [dim(`\u00bb ${padded}`)];
    default:
      return [dim(pad(label.trim(), 8))];
  }
}

function buildJobRow(j: JobState, spinnerFrame: string): StyledText {
  const isSkip = j.status === "skipped";
  const info = buildJobInfo(j);
  const chunks: ReturnType<typeof bold>[] = [
    ...buildStatusChunks(j.status, spinnerFrame),
    isSkip ? dim(pad(j.name, 24)) : bold(pad(j.name, 24)),
    isSkip ? dim(pad(j.stage, 18)) : { __isChunk: true as const, text: pad(j.stage, 18) },
    { __isChunk: true as const, text: "  " },
    isSkip ? dim(info) : { __isChunk: true as const, text: info },
  ];
  return new StyledText(chunks);
}

function statusLabel(s: string): [string, string] {
  switch (s) {
    case "passed":
      return [" PASS", cGreen];
    case "escalated":
    case "failed":
      return [" FAIL", cRed];
    case "running":
      return ["  RUN", cBlue];
    case "fixing":
      return ["  FIX", cPurple];
    case "skipped":
      return [" SKIP", ""];
    case "pending":
      return [" WAIT", ""];
    default:
      return ["   --", ""];
  }
}

// ── View Components ───────────────────────────────────────────────────────

interface ViewTree {
  renderer: CliRenderer;
  root: BoxRenderable;
  // Welcome phase
  welcomeBox: BoxRenderable;
  welcomeLogo: ASCIIFontRenderable;
  welcomeTagline: TextRenderable;
  welcomeStats: TextRenderable;
  welcomePipeline: TextRenderable;
  welcomeLoading: TextRenderable;
  welcomeNotice: TextRenderable;
  // Running/Done phase
  runBox: BoxRenderable;
  runLogo: ASCIIFontRenderable;
  runInfo: TextRenderable;
  runProgress: TextRenderable;
  runJobs: TextRenderable;
  runDriver: TextRenderable;
  runErrors: TextRenderable;
  runCommit: TextRenderable;
  runPipeline: TextRenderable;
  runFooter: TextRenderable;
}

async function buildViewTree(agent: string, repo: string): Promise<ViewTree> {
  const { root, renderer } = await createRenderer();

  // ── Welcome screen ────────────────────────────────────────────────────
  const welcomeBox = new BoxRenderable(renderer, {
    id: "welcome",
    flexDirection: "column",
    flexGrow: 1,
    alignItems: "center",
    justifyContent: "center",
    visible: true,
  });
  root.add(welcomeBox);

  const welcomeLogo = new ASCIIFontRenderable(renderer, {
    id: "welcome-logo",
    text: "STITCH",
    font: "block",
    color: [cCyan, cBlue],
  });
  welcomeBox.add(welcomeLogo);

  const welcomeTagline = new TextRenderable(renderer, {
    id: "welcome-tagline",
    content: t`${bold("Run your CI jobs locally. Fix failures with AI.")}`,
    alignSelf: "center",
  });
  welcomeBox.add(welcomeTagline);

  const welcomeStats = new TextRenderable(renderer, {
    id: "welcome-stats",
    content: t`${fg(cBlue)(bold("Agent"))} ${dim(agent)}   ${fg(cCyan)("\u00b7")}   ${fg(cCyan)(bold("Repo"))} ${dim(repo)}   ${fg(cCyan)("\u00b7")}   ${fg(cPurple)(bold(`v${VERSION}`))}`,
    alignSelf: "center",
    marginTop: 1,
  });
  welcomeBox.add(welcomeStats);

  const welcomePipeline = new TextRenderable(renderer, {
    id: "welcome-pipeline",
    content: "",
    alignSelf: "center",
    marginTop: 2,
  });
  welcomeBox.add(welcomePipeline);

  const welcomeLoading = new TextRenderable(renderer, {
    id: "welcome-loading",
    content: "",
    alignSelf: "center",
    marginTop: 1,
    live: true,
  });
  welcomeBox.add(welcomeLoading);

  const welcomeNotice = new TextRenderable(renderer, {
    id: "welcome-notice",
    content: "",
    alignSelf: "center",
    marginTop: 1,
  });
  welcomeBox.add(welcomeNotice);

  // ── Run screen ────────────────────────────────────────────────────────
  const runBox = new BoxRenderable(renderer, {
    id: "run",
    flexDirection: "column",
    flexGrow: 1,
    visible: false,
    paddingLeft: 2,
    paddingRight: 2,
  });
  root.add(runBox);

  const runLogo = new ASCIIFontRenderable(renderer, {
    id: "run-logo",
    text: "STITCH",
    font: "tiny",
    color: cCyan,
  });
  runBox.add(runLogo);

  const runInfo = new TextRenderable(renderer, {
    id: "run-info",
    content: "",
    marginTop: 1,
  });
  runBox.add(runInfo);

  const runProgress = new TextRenderable(renderer, {
    id: "run-progress",
    content: "",
    marginTop: 1,
    live: true,
  });
  runBox.add(runProgress);

  const runJobs = new TextRenderable(renderer, {
    id: "run-jobs",
    content: "",
    marginTop: 1,
    wrapMode: "none",
  });
  runBox.add(runJobs);

  const runDriver = new TextRenderable(renderer, {
    id: "run-driver",
    content: "",
    wrapMode: "char",
  });
  runBox.add(runDriver);

  const runErrors = new TextRenderable(renderer, {
    id: "run-errors",
    content: "",
    wrapMode: "char",
  });
  runBox.add(runErrors);

  const runCommit = new TextRenderable(renderer, {
    id: "run-commit",
    content: "",
  });
  runBox.add(runCommit);

  const runPipeline = new TextRenderable(renderer, {
    id: "run-pipeline",
    content: "",
    marginTop: 1,
    alignSelf: "center",
  });
  runBox.add(runPipeline);

  const runFooter = new TextRenderable(renderer, {
    id: "run-footer",
    content: "",
    marginTop: 1,
  });
  runBox.add(runFooter);

  return {
    renderer,
    root,
    welcomeBox,
    welcomeLogo,
    welcomeTagline,
    welcomeStats,
    welcomePipeline,
    welcomeLoading,
    welcomeNotice,
    runBox,
    runLogo,
    runInfo,
    runProgress,
    runJobs,
    runDriver,
    runErrors,
    runCommit,
    runPipeline,
    runFooter,
  };
}

// ── Update Functions ──────────────────────────────────────────────────────

function updateWelcome(view: ViewTree, tuiState: TuiState, spinner: Spinner): void {
  view.welcomePipeline.content = buildPipelineText(tuiState.pipelineStep);
  view.welcomeLoading.content = t`${fg(cCyan)(spinner.frame)}  ${dim(tuiState.loadingMsg)}`;
  view.welcomeNotice.content = tuiState.notice
    ? t`${fg(cOrange)("⚠")}  ${fg(cOrange)(tuiState.notice)}`
    : "";
}

function updateRunView(
  view: ViewTree,
  tuiState: TuiState,
  agent: string,
  repo: string,
  spinner: Spinner,
): void {
  const state = tuiState.state;
  const cols = view.renderer.width;
  const w = Math.max(40, cols - 8);
  const barW = Math.max(20, Math.min(w - 25, 60));

  const runnable = state.jobs.filter((j) => j.status !== "skipped");
  const skipped = state.jobs.filter((j) => j.status === "skipped");
  const done = runnable.filter((j) => ["passed", "escalated", "failed"].includes(j.status)).length;
  const running = runnable.filter((j) => ["running", "fixing"].includes(j.status)).length;
  const isRunning = state.phase === "running";
  const isDone = state.phase === "done";
  const allPassed = isDone && state.lastReport?.failed === 0;

  const ms = Date.now() - state.startTime;
  const pct = isDone
    ? 100
    : Math.round(
        Math.min(
          ((done + running * 0.5) / (runnable.length || 1)) * 100 + Math.min(ms / 1000, 10),
          99,
        ),
      );
  const barColor = allPassed ? cGreen : isDone ? cRed : cBlue;

  // Info line
  const infoChunks = [
    dim("Agent: "),
    fg(cBlue)(bold(agent)),
    dim("  "),
    dim("Repo: "),
    fg(cCyan)(bold(repo)),
  ];
  if (state.runCount > 0) {
    infoChunks.push(dim("  Run: "), fg(cPurple)(`#${state.runCount}`));
  }
  infoChunks.push(dim("  Jobs: "), fg(cCyan)(String(runnable.length)));
  if (skipped.length > 0) {
    infoChunks.push(dim("  Skipped: "), dim(String(skipped.length)));
  }
  view.runInfo.content = new StyledText(infoChunks);

  // Progress line
  const progressChunks = [styledProgressBar(pct, barW, barColor)];
  progressChunks.push(new StyledText([dim(` ${done}/${runnable.length} `), dim(`${pct}%`)]));
  if (isRunning) {
    progressChunks.push(
      new StyledText([fg(cCyan)(` ${spinner.frame} `), fg(cCyan)(formatElapsed(ms))]),
    );
  }
  if (isDone && state.lastReport) {
    progressChunks.push(new StyledText([dim(` ${state.lastReport.elapsed.toFixed(1)}s`)]));
  }
  // Flatten: combine all StyledText chunks
  const allProgressChunks = progressChunks.flatMap((st) =>
    st instanceof StyledText ? st.chunks : [],
  );
  view.runProgress.content = new StyledText(allProgressChunks);

  // Job table
  const jobLines: StyledText[] = [];
  jobLines.push(styledLine(w));
  jobLines.push(
    new StyledText([
      fg(cBlue)(bold(pad("STATUS", 8))),
      fg(cBlue)(bold(pad("JOB", 24))),
      fg(cBlue)(bold(pad("STAGE", 18))),
      { __isChunk: true as const, text: "  " },
      fg(cBlue)(bold("INFO")),
    ]),
  );
  jobLines.push(styledLine(w));
  for (const j of runnable) jobLines.push(buildJobRow(j, spinner.frame));
  if (skipped.length > 0) {
    jobLines.push(styledLine(w));
    for (const j of skipped) jobLines.push(buildJobRow(j, spinner.frame));
  }
  jobLines.push(styledLine(w));
  // Join with newlines
  const jobChunks = jobLines.flatMap((st, i) => {
    const chunks = [...st.chunks];
    if (i < jobLines.length - 1) chunks.push({ __isChunk: true as const, text: "\n" });
    return chunks;
  });
  view.runJobs.content = new StyledText(jobChunks);

  updateDriverPanel(view, state, spinner);
  updateErrorPanel(view, state, isDone);
  updateCommitPanel(view, state, isDone);
  view.runPipeline.content = buildPipelineText(tuiState.pipelineStep);
  updateFooter(view, state, w, isRunning, isDone, allPassed);
}

function updateDriverPanel(view: ViewTree, state: AppState, spinner: Spinner): void {
  if (!state.fixing) {
    view.runDriver.visible = false;
    return;
  }
  const LOG_ROWS = 12;
  const driverChunks: ReturnType<typeof bold>[] = [
    fg(cPurple)(spinner.frame),
    fg(cPurple)(bold(` ${state.fixing.label}`)),
    dim("  fixing with "),
    fg(cBlue)(bold(state.fixing.driver)),
    { __isChunk: true as const, text: "\n" },
  ];
  const logLines = (state.fixing.log || "")
    .trim()
    .split("\n")
    .filter(
      (l) => !l.includes("\u2192") && !l.includes("STITCH -") && !l.includes("\u2500\u2500\u2500"),
    )
    .slice(-LOG_ROWS);
  for (const l of logLines) {
    const lo = l.toLowerCase();
    const isErr = ["error", "fail", "assert", "exception"].some((k) => lo.includes(k));
    driverChunks.push(l.startsWith("> ") ? fg(cCyan)(l) : isErr ? fg(cRed)(l) : dim(l));
    driverChunks.push({ __isChunk: true as const, text: "\n" });
  }
  for (let i = logLines.length; i < LOG_ROWS; i++) {
    driverChunks.push({ __isChunk: true as const, text: "\n" });
  }
  view.runDriver.content = new StyledText(driverChunks);
  view.runDriver.visible = true;
}

function updateErrorPanel(view: ViewTree, state: AppState, isDone: boolean): void {
  if (!isDone) {
    view.runErrors.visible = false;
    return;
  }
  const failed = state.jobs.filter(
    (j) => (j.status === "escalated" || j.status === "failed") && j.errorLog,
  );
  if (failed.length === 0) {
    view.runErrors.visible = false;
    return;
  }
  const errChunks: ReturnType<typeof bold>[] = [];
  for (const j of failed) {
    errChunks.push({ __isChunk: true as const, text: "\n" });
    errChunks.push(fg(cRed)(bold(`x ${j.name}`)));
    errChunks.push({ __isChunk: true as const, text: "\n" });
    for (const l of j.errorLog.trim().split("\n").slice(-6)) {
      errChunks.push(l.toLowerCase().includes("error") ? fg(cRed)(l) : dim(l));
      errChunks.push({ __isChunk: true as const, text: "\n" });
    }
  }
  view.runErrors.content = new StyledText(errChunks);
  view.runErrors.visible = true;
}

function updateCommitPanel(view: ViewTree, state: AppState, isDone: boolean): void {
  if (!isDone || !state.lastReport) {
    view.runCommit.visible = false;
    return;
  }
  const report = state.lastReport;
  if (report.commitSha) {
    const commitChunks = [
      fg(cGreen)("*"),
      dim(" committed "),
      fg(cOrange)(bold(report.commitSha.slice(0, 8))),
      dim(` fix(stitch): ${report.fixed.join(", ")}`),
    ];
    if (report.pushed) {
      commitChunks.push(fg(cGreen)(" pushed"));
    } else if (report.reason === "push_failed") {
      commitChunks.push(fg(cRed)(" push failed"));
    }
    view.runCommit.content = new StyledText(commitChunks);
    view.runCommit.visible = true;
    return;
  }
  const skipMsg = commitSkipMessage(report.reason);
  if (!skipMsg) {
    view.runCommit.visible = false;
    return;
  }
  view.runCommit.content = new StyledText([dim("* "), dim(skipMsg)]);
  view.runCommit.visible = true;
}

function commitSkipMessage(reason?: CommitPushReason): string | null {
  switch (reason) {
    case "dirty_pre_run":
      return "skipped commit: uncommitted changes present before run";
    case "run_failed":
      return "skipped commit: run failed";
    case "no_fixed_jobs":
      return "no commit: no fixes applied";
    case "nothing_staged":
      return "skipped commit: nothing staged";
    case "commit_failed":
      return "commit failed";
    default:
      return null;
  }
}

function buildStatusText(
  isRunning: boolean,
  isDone: boolean,
  allPassed: boolean,
  report: AppState["lastReport"],
): string {
  if (allPassed && report) return `STITCH - All ${report.passed} jobs passed`;
  if (isDone && report) return `STITCH - ${report.failed} failed, ${report.passed} passed`;
  if (isRunning) return "STITCH - Running";
  return "STITCH";
}

function updateFooter(
  view: ViewTree,
  state: AppState,
  w: number,
  isRunning: boolean,
  isDone: boolean,
  allPassed: boolean,
): void {
  const statusText = buildStatusText(isRunning, isDone, allPassed, state.lastReport);
  const statusChunk = allPassed
    ? fg(cGreen)(bold(statusText))
    : isDone
      ? fg(cRed)(bold(statusText))
      : fg(cBlue)(bold(statusText));
  const cmdsText = isDone ? "enter run again  q quit" : "q quit  ctrl+c abort";
  const cmdsChunk = isDone
    ? new StyledText([bold("enter"), dim(" run again  "), bold("q"), dim(" quit")])
    : new StyledText([bold("q"), dim(" quit  "), bold("ctrl+c"), dim(" abort")]);
  const gap = Math.max(2, w - statusText.length - cmdsText.length);
  view.runFooter.content = new StyledText([
    ...styledLine(w).chunks,
    { __isChunk: true as const, text: "\n" },
    statusChunk,
    { __isChunk: true as const, text: " ".repeat(gap) },
    ...cmdsChunk.chunks,
  ]);
}

// ── Public API ─────────────────────────────────────────────────────────────

export class StitchUI {
  private tuiState: TuiState;
  private spinner: Spinner;
  private view: ViewTree | null = null;
  private keypressHandler: ((event: { name?: string; ctrl?: boolean }) => void) | null = null;
  private sigintHandler: (() => void) | null = null;
  private renderTimer: ReturnType<typeof setInterval> | null = null;
  private agent: string;
  private repo: string;
  startedAt = 0;

  constructor(agent: string, repo: string) {
    this.tuiState = new TuiState();
    this.spinner = new Spinner();
    this.agent = agent;
    this.repo = repo.split("/").slice(-2).join("/");
  }

  setLoading(msg: string, step?: number) {
    this.tuiState.loadingMsg = msg;
    if (step !== undefined) this.tuiState.pipelineStep = step;
    this.refresh();
  }

  setNotice(msg: string) {
    this.tuiState.notice = msg;
    this.refresh();
  }

  initJobs(jobs: CIJob[]) {
    this.tuiState.initJobs(jobs);
    if (this.view) {
      this.view.welcomeBox.visible = false;
      this.view.runBox.visible = true;
    }
    this.refresh();
  }

  async start() {
    this.startedAt = Date.now();
    this.view = await buildViewTree(this.agent, this.repo);

    // Start renderer
    this.view.renderer.start();

    // Start spinner, triggers refresh on each tick
    this.spinner.start(() => this.refresh());

    // Periodic refresh for timer updates (every 200ms)
    this.renderTimer = setInterval(() => this.refresh(), 200);

    // Initial render
    this.refresh();

    // Keyboard handler via OpenTUI's keypress API
    const quit = () => {
      this.stop();
      if (this.tuiState.onQuit) this.tuiState.onQuit();
      process.exit(0);
    };

    this.keypressHandler = (event) => {
      const name = event.name;
      if (name === "c" && event.ctrl) {
        if (this.tuiState.state.phase !== "done" && this.tuiState.onAbort) {
          this.tuiState.onAbort();
          return;
        }
        quit();
        return;
      }
      if (name === "q") {
        quit();
        return;
      }
      if (
        (name === "return" || name === "enter" || name === "r") &&
        this.tuiState.state.phase === "done"
      ) {
        this.tuiState.onRerun?.();
      }
    };
    this.view.renderer.keyInput.on("keypress", this.keypressHandler);

    // Safety net: SIGINT (e.g., when raw mode is off or signal bypasses stdin parser)
    this.sigintHandler = quit;
    process.on("SIGINT", this.sigintHandler);
  }

  stop() {
    this.spinner.stop();
    if (this.renderTimer) {
      clearInterval(this.renderTimer);
      this.renderTimer = null;
    }
    if (this.keypressHandler && this.view) {
      this.view.renderer.keyInput.off("keypress", this.keypressHandler);
      this.keypressHandler = null;
    }
    if (this.sigintHandler) {
      process.removeListener("SIGINT", this.sigintHandler);
      this.sigintHandler = null;
    }
    if (this.view) {
      this.view.renderer.destroy();
      this.view = null;
    }
  }

  markDone(
    report: RunReport,
    commitSha: string | null,
    pushed: boolean,
    reason?: CommitPushReason,
  ) {
    this.tuiState.markDone(report, commitSha, pushed, reason);
    this.refresh();
  }

  setOnAbort(fn: (() => void) | null): void {
    this.tuiState.onAbort = fn;
  }

  waitForRerun(signal?: AbortSignal): Promise<"rerun" | "quit" | "aborted"> {
    return new Promise((resolve) => {
      if (signal?.aborted) {
        resolve("aborted");
        return;
      }
      const onAbort = () => {
        this.tuiState.onRerun = null;
        this.tuiState.onQuit = null;
        resolve("aborted");
      };
      signal?.addEventListener("abort", onAbort, { once: true });
      this.tuiState.onRerun = () => {
        signal?.removeEventListener("abort", onAbort);
        resolve("rerun");
      };
      this.tuiState.onQuit = () => {
        signal?.removeEventListener("abort", onAbort);
        resolve("quit");
      };
    });
  }

  get callback(): RunnerCallback {
    return {
      jobStarted: (n, a, m) => {
        this.tuiState.jobStarted(n, a, m);
        this.refresh();
      },
      jobLogUpdate: () => {},
      jobFinished: (n, r) => {
        this.tuiState.jobFinished(n, r);
        this.refresh();
      },
      driverStarted: (n, d) => {
        this.tuiState.driverStarted(n, d);
        this.refresh();
      },
      driverLogUpdate: (n, l) => {
        this.tuiState.driverLogUpdate(n, l);
        // No immediate refresh - the 200ms timer handles it
      },
    };
  }

  private refresh(): void {
    if (!this.view) return;
    const state = this.tuiState.state;
    if (state.phase === "welcome") {
      updateWelcome(this.view, this.tuiState, this.spinner);
    } else {
      updateRunView(this.view, this.tuiState, this.agent, this.repo, this.spinner);
    }
    this.view.renderer.requestRender();
  }
}
