import {
  constants,
  accessSync,
  existsSync,
  mkdirSync,
  rmSync,
  statSync,
  writeFileSync,
} from "node:fs";
import { join, resolve } from "node:path";
import { detectPlatform } from "../core/ci-detect.js";
import { parseCIConfig } from "../core/ci-parser.js";
import { which } from "../util.js";

export interface DoctorOptions {
  repo: string;
  output: string;
}

export type CheckStatus = "pass" | "fail" | "warn" | "skip";

export interface CheckResult {
  name: string;
  status: CheckStatus;
  message: string;
}

const SUPPORTED_AGENTS = ["claude", "codex"] as const;

// Same palette as src/core/ui/run-ui.ts
const cGreen = "52;211;153";
const cRed = "248;113;113";
const cCyan = "122;162;247";
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

function checkRuntime(): CheckResult {
  const bunVersion = process.versions.bun ?? null;
  if (bunVersion) {
    const major = Number.parseInt(bunVersion.split(".")[0] ?? "0", 10);
    if (major >= 1) return { name: "Runtime", status: "pass", message: `bun ${bunVersion}` };
    return {
      name: "Runtime",
      status: "fail",
      message: `bun ${bunVersion} too old. Upgrade to bun 1.x`,
    };
  }
  const nodeMatch = process.version.match(/^v(\d+)/);
  const nodeMajor = nodeMatch ? Number.parseInt(nodeMatch[1] ?? "0", 10) : 0;
  if (nodeMajor >= 20) {
    return { name: "Runtime", status: "pass", message: `node ${process.version}` };
  }
  return {
    name: "Runtime",
    status: "fail",
    message: `node ${process.version} too old. Upgrade to node 20+ or bun 1.x`,
  };
}

function checkGitRepo(repoRoot: string): CheckResult {
  const gitDir = join(repoRoot, ".git");
  if (existsSync(gitDir)) {
    return { name: "Git repository", status: "pass", message: ".git found" };
  }
  return {
    name: "Git repository",
    status: "fail",
    message: "Not a git repository. Run `git init` first",
  };
}

function checkCIConfig(repoRoot: string): CheckResult {
  const found: string[] = [];
  if (existsSync(join(repoRoot, ".gitlab-ci.yml"))) found.push("gitlab");
  if (existsSync(join(repoRoot, "bitbucket-pipelines.yml"))) found.push("bitbucket");
  try {
    const gh = join(repoRoot, ".github", "workflows");
    if (existsSync(gh) && statSync(gh).isDirectory()) found.push("github");
  } catch {
    // ignore
  }
  if (found.length === 0) {
    return {
      name: "CI config",
      status: "fail",
      message: "No CI config found. Run `stitch generate` to create one",
    };
  }
  if (found.length > 1) {
    return {
      name: "CI config",
      status: "warn",
      message: `Multiple platforms detected (${found.join(", ")}). Set one to avoid ambiguity`,
    };
  }
  return { name: "CI config", status: "pass", message: `${found[0]} detected` };
}

function checkAgents(): CheckResult {
  const found = SUPPORTED_AGENTS.filter((a) => which(a));
  if (found.length > 0) {
    return { name: "Agent CLI", status: "pass", message: `${found.join(", ")} available` };
  }
  return {
    name: "Agent CLI",
    status: "fail",
    message:
      "No agent CLI in PATH. Install Claude Code (`npm i -g @anthropic-ai/claude-code`) or Codex",
  };
}

function checkJobsParseable(repoRoot: string, ciStatus: CheckStatus): CheckResult {
  if (ciStatus === "fail") {
    return { name: "Jobs parseable", status: "skip", message: "no CI config" };
  }
  try {
    const platform = detectPlatform(repoRoot);
    const jobs = parseCIConfig(repoRoot, platform);
    if (jobs.length === 0) {
      return {
        name: "Jobs parseable",
        status: "fail",
        message: "CI config found but no jobs detected. Check YAML syntax",
      };
    }
    return { name: "Jobs parseable", status: "pass", message: `${jobs.length} jobs found` };
  } catch (err) {
    return {
      name: "Jobs parseable",
      status: "fail",
      message: `Parse error: ${(err as Error).message}`,
    };
  }
}

function checkWritePermissions(repoRoot: string): CheckResult {
  const cacheDir = join(repoRoot, ".stitch");
  try {
    if (existsSync(cacheDir)) {
      const s = statSync(cacheDir);
      if (!s.isDirectory()) {
        return {
          name: "Write permissions",
          status: "fail",
          message: ".stitch exists but is not a directory",
        };
      }
      accessSync(cacheDir, constants.W_OK);
      return { name: "Write permissions", status: "pass", message: ".stitch/ writable" };
    }
    mkdirSync(cacheDir, { recursive: true });
    const probe = join(cacheDir, ".doctor-probe");
    try {
      writeFileSync(probe, "");
    } finally {
      try {
        rmSync(probe, { force: true });
      } catch {
        // ignore
      }
    }
    return { name: "Write permissions", status: "pass", message: ".stitch/ writable" };
  } catch (err) {
    return {
      name: "Write permissions",
      status: "fail",
      message: `Cannot write to .stitch: ${(err as Error).message}`,
    };
  }
}

function runChecks(repoRoot: string): CheckResult[] {
  const runtime = checkRuntime();
  const git = checkGitRepo(repoRoot);
  const ci = checkCIConfig(repoRoot);
  const agents = checkAgents();
  const jobs = checkJobsParseable(repoRoot, ci.status);
  const perms = checkWritePermissions(repoRoot);
  return [runtime, git, ci, agents, jobs, perms];
}

function statusIcon(s: CheckStatus): string {
  switch (s) {
    case "pass":
      return fg(cGreen, "\u2713");
    case "fail":
      return fg(cRed, "\u2717");
    case "warn":
      return fg(cOrange, "!");
    case "skip":
      return dim("\u00bb");
  }
}

function statusLabel(s: CheckStatus): string {
  switch (s) {
    case "pass":
      return fg(cGreen, pad("PASS", 4));
    case "fail":
      return fg(cRed, pad("FAIL", 4));
    case "warn":
      return fg(cOrange, pad("WARN", 4));
    case "skip":
      return dim(pad("SKIP", 4));
  }
}

function printHuman(results: CheckResult[]): void {
  const line = dim("\u2500".repeat(60));
  process.stdout.write(`\n  ${fg(cCyan, bold("STITCH"))} ${dim("Doctor")}\n\n`);
  process.stdout.write(`  ${line}\n`);
  for (const r of results) {
    const row = `  ${statusIcon(r.status)} ${statusLabel(r.status)}  ${bold(pad(r.name, 22))}${dim(r.message)}\n`;
    process.stdout.write(row);
  }
  process.stdout.write(`  ${line}\n`);

  const passed = results.filter((r) => r.status === "pass").length;
  const failed = results.filter((r) => r.status === "fail").length;
  const skipped = results.filter((r) => r.status === "skip").length;

  let summary: string;
  if (failed === 0) {
    summary = fg(cGreen, bold(`STITCH - All ${passed} checks passed`));
  } else {
    summary = fg(cRed, bold(`STITCH - ${failed} failed, ${passed} passed`));
  }
  const skipStr = skipped > 0 ? dim(`, ${skipped} skipped`) : "";
  process.stdout.write(`  ${summary}${skipStr}\n\n`);
}

export async function runDoctorCommand(opts: DoctorOptions): Promise<number> {
  const repoRoot = resolve(opts.repo);
  if (!existsSync(repoRoot)) {
    console.error(`Error: repo path not found: ${repoRoot}`);
    return 2;
  }

  const results = runChecks(repoRoot);

  if (opts.output === "json") {
    process.stdout.write(`${JSON.stringify({ checks: results }, null, 2)}\n`);
  } else {
    printHuman(results);
  }

  return results.some((r) => r.status === "fail") ? 1 : 0;
}
