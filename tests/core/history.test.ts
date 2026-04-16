import { existsSync, mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { ROTATE_LINE_THRESHOLD, readHistory, recordRun } from "../../src/core/history.js";
import { type GitSnapshot, type JobResult, RunReport } from "../../src/core/models.js";

const SNAP: GitSnapshot = { clean: true, branch: "main", hasRemote: true, ahead: 0 };

function mkResult(
  name: string,
  status: JobResult["status"],
  attempts = 1,
  errorLog = "",
): JobResult {
  return { name, status, attempts, driver: null, errorLog, skipReason: null };
}

function mkReport(...results: JobResult[]) {
  return new RunReport(results, "claude");
}

describe("history.recordRun", () => {
  let tmp: string;

  beforeEach(() => {
    tmp = join(tmpdir(), `stitch-history-${Date.now()}-${Math.random().toString(36).slice(2)}`);
    mkdirSync(tmp, { recursive: true });
  });

  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
  });

  it("collapses 100 consecutive identical passes into one ongoing entry", () => {
    for (let i = 0; i < 100; i++) {
      recordRun(mkReport(mkResult("lint", "passed")), {
        repoRoot: tmp,
        agent: "claude",
        snap: SNAP,
        commitSha: null,
      });
    }

    const view = readHistory(tmp);
    expect(view.finalized).toHaveLength(0);
    expect(view.ongoing).toHaveLength(1);
    expect(view.ongoing[0]?.runs).toBe(100);
    expect(view.ongoing[0]?.status).toBe("passed");
    // Log file should not exist (no streak ever broke)
    expect(existsSync(join(tmp, ".stitch", "history.jsonl"))).toBe(false);
  });

  it("flushes prior streak when status changes", () => {
    recordRun(mkReport(mkResult("lint", "passed")), {
      repoRoot: tmp,
      agent: "claude",
      snap: SNAP,
      commitSha: null,
    });
    recordRun(mkReport(mkResult("lint", "passed")), {
      repoRoot: tmp,
      agent: "claude",
      snap: SNAP,
      commitSha: null,
    });
    // Now a fix happens
    recordRun(mkReport(mkResult("lint", "passed", 2)), {
      repoRoot: tmp,
      agent: "claude",
      snap: SNAP,
      commitSha: "abc123",
    });

    const view = readHistory(tmp);
    expect(view.finalized).toHaveLength(1);
    expect(view.finalized[0]?.status).toBe("passed");
    expect(view.finalized[0]?.runs).toBe(2);
    expect(view.ongoing).toHaveLength(1);
    expect(view.ongoing[0]?.status).toBe("fixed");
    expect(view.ongoing[0]?.commitSha).toBe("abc123");
  });

  it("treats different errorFirstLine as a new streak", () => {
    recordRun(mkReport(mkResult("test", "escalated", 3, "Error: missing import")), {
      repoRoot: tmp,
      agent: "claude",
      snap: SNAP,
      commitSha: null,
    });
    recordRun(mkReport(mkResult("test", "escalated", 3, "Error: missing import")), {
      repoRoot: tmp,
      agent: "claude",
      snap: SNAP,
      commitSha: null,
    });
    recordRun(mkReport(mkResult("test", "escalated", 3, "Error: type mismatch")), {
      repoRoot: tmp,
      agent: "claude",
      snap: SNAP,
      commitSha: null,
    });

    const view = readHistory(tmp);
    expect(view.finalized).toHaveLength(1);
    expect(view.finalized[0]?.runs).toBe(2);
    expect(view.finalized[0]?.errorFirstLine).toBe("Error: missing import");
    expect(view.ongoing[0]?.errorFirstLine).toBe("Error: type mismatch");
    expect(view.ongoing[0]?.runs).toBe(1);
  });

  it("ignores skipped jobs", () => {
    const result: JobResult = {
      name: "deploy",
      status: "skipped",
      attempts: 0,
      driver: null,
      errorLog: "",
      skipReason: "infra",
    };
    recordRun(mkReport(result), {
      repoRoot: tmp,
      agent: "claude",
      snap: SNAP,
      commitSha: null,
    });
    const view = readHistory(tmp);
    expect(view.finalized).toHaveLength(0);
    expect(view.ongoing).toHaveLength(0);
  });

  it("classifies passed-with-attempts>1 as fixed", () => {
    recordRun(mkReport(mkResult("typecheck", "passed", 2)), {
      repoRoot: tmp,
      agent: "claude",
      snap: SNAP,
      commitSha: "deadbeef",
    });
    const view = readHistory(tmp);
    expect(view.ongoing[0]?.status).toBe("fixed");
    expect(view.ongoing[0]?.commitSha).toBe("deadbeef");
  });

  it("filters by job name on read", () => {
    recordRun(mkReport(mkResult("lint", "passed"), mkResult("test", "passed")), {
      repoRoot: tmp,
      agent: "claude",
      snap: SNAP,
      commitSha: null,
    });
    recordRun(mkReport(mkResult("lint", "passed", 2)), {
      repoRoot: tmp,
      agent: "claude",
      snap: SNAP,
      commitSha: null,
    });

    const view = readHistory(tmp, { job: "lint" });
    expect(view.finalized.every((e) => e.job === "lint")).toBe(true);
    expect(view.ongoing.every((e) => e.job === "lint")).toBe(true);
  });

  it("rotates the log file when it exceeds the line threshold", () => {
    const dir = join(tmp, ".stitch");
    mkdirSync(dir, { recursive: true });
    const log = join(dir, "history.jsonl");
    const padding =
      `${JSON.stringify({ v: 1, job: "x", status: "passed", agent: null, attempts: 1, errorFirstLine: null, branch: null, commitSha: null, runs: 1, firstAt: "2026-01-01T00:00:00.000Z", lastAt: "2026-01-01T00:00:00.000Z" })}\n`.repeat(
        ROTATE_LINE_THRESHOLD,
      );
    writeFileSync(log, padding);

    // Trigger any append by making a streak break
    recordRun(mkReport(mkResult("lint", "passed")), {
      repoRoot: tmp,
      agent: "claude",
      snap: SNAP,
      commitSha: null,
    });
    recordRun(mkReport(mkResult("lint", "escalated", 3, "boom")), {
      repoRoot: tmp,
      agent: "claude",
      snap: SNAP,
      commitSha: null,
    });

    expect(existsSync(join(dir, "history.1.jsonl"))).toBe(true);
  });

  it("survives a corrupt head file by starting fresh", () => {
    const dir = join(tmp, ".stitch");
    mkdirSync(dir, { recursive: true });
    writeFileSync(join(dir, "history-head.json"), "not valid json {{{");

    recordRun(mkReport(mkResult("lint", "passed")), {
      repoRoot: tmp,
      agent: "claude",
      snap: SNAP,
      commitSha: null,
    });

    const view = readHistory(tmp);
    expect(view.ongoing).toHaveLength(1);
    expect(view.ongoing[0]?.status).toBe("passed");
  });

  it("writes head as pretty JSON with schema version", () => {
    recordRun(mkReport(mkResult("lint", "passed")), {
      repoRoot: tmp,
      agent: "claude",
      snap: SNAP,
      commitSha: null,
    });
    const head = JSON.parse(readFileSync(join(tmp, ".stitch", "history-head.json"), "utf-8"));
    expect(head.v).toBe(1);
    expect(head.jobs.lint).toBeDefined();
  });
});
