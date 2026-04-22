import { describe, expect, it } from "vitest";
import { RunReport, isCommittable, isPushable } from "../../src/core/models.js";
import type { GitSnapshot, JobResult } from "../../src/core/models.js";

function result(overrides: Partial<JobResult> = {}): JobResult {
  return {
    name: "test",
    status: "passed",
    attempts: 1,
    driver: null,
    errorLog: "",
    skipReason: null,
    filesModified: false,
    ...overrides,
  };
}

describe("RunReport", () => {
  describe("overallStatus", () => {
    it("returns passed when all non-skipped jobs pass", () => {
      const report = new RunReport([
        result({ name: "lint", status: "passed" }),
        result({ name: "test", status: "passed" }),
      ]);
      expect(report.overallStatus).toBe("passed");
    });

    it("returns failed when one job is escalated", () => {
      const report = new RunReport([
        result({ name: "lint", status: "passed" }),
        result({ name: "test", status: "escalated" }),
      ]);
      expect(report.overallStatus).toBe("failed");
    });

    it("returns passed when all jobs are skipped", () => {
      const report = new RunReport([
        result({ name: "deploy", status: "skipped" }),
        result({ name: "sync", status: "skipped" }),
      ]);
      expect(report.overallStatus).toBe("passed");
    });

    it("ignores skipped jobs when determining status", () => {
      const report = new RunReport([
        result({ name: "lint", status: "passed" }),
        result({ name: "deploy", status: "skipped" }),
      ]);
      expect(report.overallStatus).toBe("passed");
    });
  });

  describe("fixedJobs", () => {
    it("returns jobs that passed after a driver applied edits", () => {
      const report = new RunReport([
        result({ name: "lint", status: "passed", filesModified: false }),
        result({ name: "test", status: "passed", filesModified: true }),
        result({ name: "build", status: "escalated", filesModified: true }),
      ]);
      expect(report.fixedJobs).toEqual(["test"]);
    });

    it("ignores attempts count when deriving fixedJobs", () => {
      const report = new RunReport([
        result({ name: "a", status: "passed", attempts: 3, filesModified: false }),
        result({ name: "b", status: "passed", attempts: 1, filesModified: true }),
      ]);
      expect(report.fixedJobs).toEqual(["b"]);
    });

    it("returns empty array when no jobs modified files", () => {
      const report = new RunReport([
        result({ name: "lint", status: "passed", filesModified: false }),
      ]);
      expect(report.fixedJobs).toEqual([]);
    });
  });

  describe("exitCode", () => {
    it("returns 0 when passed", () => {
      const report = new RunReport([result({ status: "passed" })]);
      expect(report.exitCode()).toBe(0);
    });

    it("returns 1 when failed", () => {
      const report = new RunReport([result({ status: "escalated" })]);
      expect(report.exitCode()).toBe(1);
    });
  });

  describe("toDict", () => {
    it("serializes to a plain object with snake_case keys", () => {
      const report = new RunReport(
        [result({ name: "lint", status: "passed", attempts: 1, skipReason: null })],
        "claude",
      );
      const d = report.toDict();
      expect(d.agent).toBe("claude");
      expect(d.overall_status).toBe("passed");
      expect(Array.isArray(d.jobs)).toBe(true);
      const jobs = d.jobs as Record<string, unknown>[];
      expect(jobs[0]?.name).toBe("lint");
      expect(jobs[0]?.skip_reason).toBeNull();
      expect(jobs[0]?.error_log).toBe("");
      expect(jobs[0]?.files_modified).toBe(false);
    });
  });
});

describe("GitSnapshot helpers", () => {
  it("committable when clean and on a branch", () => {
    const snap: GitSnapshot = { clean: true, branch: "main", hasRemote: true, ahead: 0 };
    expect(isCommittable(snap)).toBe(true);
  });

  it("not committable when dirty", () => {
    const snap: GitSnapshot = { clean: false, branch: "main", hasRemote: true, ahead: 0 };
    expect(isCommittable(snap)).toBe(false);
  });

  it("not committable when detached HEAD", () => {
    const snap: GitSnapshot = { clean: true, branch: null, hasRemote: false, ahead: 0 };
    expect(isCommittable(snap)).toBe(false);
  });

  it("pushable when committable and not ahead", () => {
    const snap: GitSnapshot = { clean: true, branch: "main", hasRemote: true, ahead: 0 };
    expect(isPushable(snap)).toBe(true);
  });

  it("not pushable when ahead of remote", () => {
    const snap: GitSnapshot = { clean: true, branch: "main", hasRemote: true, ahead: 2 };
    expect(isPushable(snap)).toBe(false);
  });

  it("pushable when no remote (new branch)", () => {
    const snap: GitSnapshot = { clean: true, branch: "feat", hasRemote: false, ahead: 0 };
    expect(isPushable(snap)).toBe(true);
  });
});
