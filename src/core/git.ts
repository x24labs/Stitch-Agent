import { execaSync } from "execa";
import type { CommitResult, GitSnapshot, PushResult } from "./models.js";

function run(args: string[], cwd: string) {
  try {
    const result = execaSync("git", args, { cwd, reject: false });
    return { returncode: result.exitCode, stdout: result.stdout, stderr: result.stderr };
  } catch {
    return { returncode: -1, stdout: "", stderr: "git command failed" };
  }
}

export function snapshot(repoRoot: string): GitSnapshot {
  // Clean check
  const status = run(["status", "--porcelain"], repoRoot);
  const clean = status.returncode === 0 && status.stdout.trim() === "";

  // Branch name
  const branchResult = run(["rev-parse", "--abbrev-ref", "HEAD"], repoRoot);
  if (branchResult.returncode !== 0) {
    return { clean: false, branch: null, hasRemote: false, ahead: 0 };
  }
  const branch = branchResult.stdout.trim();
  if (branch === "HEAD") {
    return { clean, branch: null, hasRemote: false, ahead: 0 };
  }

  // Remote tracking
  const upstream = run(["rev-parse", "--abbrev-ref", "@{u}"], repoRoot);
  if (upstream.returncode !== 0) {
    return { clean, branch, hasRemote: false, ahead: 0 };
  }

  // Ahead count
  const aheadResult = run(["rev-list", "@{u}..HEAD", "--count"], repoRoot);
  let ahead = 0;
  if (aheadResult.returncode === 0) {
    const parsed = parseInt(aheadResult.stdout.trim(), 10);
    if (!isNaN(parsed)) ahead = parsed;
  }

  return { clean, branch, hasRemote: true, ahead };
}

export function commit(repoRoot: string, fixedJobs: string[]): CommitResult {
  run(["add", "-u"], repoRoot);

  // Check if anything staged
  const diffCheck = run(["diff", "--cached", "--quiet"], repoRoot);
  if (diffCheck.returncode === 0) {
    return { ok: false, sha: "", message: "no changes to commit" };
  }

  const message = `fix(stitch): ${fixedJobs.join(", ")}`;
  const result = run(["commit", "-m", message], repoRoot);
  if (result.returncode !== 0) {
    return { ok: false, sha: "", message: result.stderr.trim() };
  }

  const shaResult = run(["rev-parse", "HEAD"], repoRoot);
  const sha = shaResult.returncode === 0 ? shaResult.stdout.trim() : "";

  return { ok: true, sha, message };
}

export function push(repoRoot: string): PushResult {
  // Check if upstream exists
  const upstream = run(["rev-parse", "--abbrev-ref", "@{u}"], repoRoot);
  if (upstream.returncode !== 0) {
    const branch = run(["rev-parse", "--abbrev-ref", "HEAD"], repoRoot);
    if (branch.returncode !== 0) {
      return { ok: false, error: "cannot determine current branch" };
    }
    const result = run(["push", "-u", "origin", branch.stdout.trim()], repoRoot);
    if (result.returncode !== 0) {
      return { ok: false, error: result.stderr.trim() };
    }
    return { ok: true, error: "" };
  }

  const result = run(["push"], repoRoot);
  if (result.returncode !== 0) {
    return { ok: false, error: result.stderr.trim() };
  }
  return { ok: true, error: "" };
}
