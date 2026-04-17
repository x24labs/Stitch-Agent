import { createHash } from "node:crypto";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { execa } from "execa";
import { which } from "../util.js";
import type { CIJob } from "./models.js";

const CACHE_DIR = ".stitch";
const CACHE_FILE = "jobs.json";

interface FilterConfig {
  only: string[] | null;
  exclude?: string[] | null;
}

function cachePath(repoRoot: string): string {
  return join(repoRoot, CACHE_DIR, CACHE_FILE);
}

function jobNamesHash(names: string[]): string {
  return createHash("sha256")
    .update([...names].sort().join("\n"))
    .digest("hex")
    .slice(0, 16);
}

export function loadCache(repoRoot: string, jobNames: string[]): Record<string, string> | null {
  const path = cachePath(repoRoot);
  if (!existsSync(path)) return null;

  let data: Record<string, unknown>;
  try {
    data = JSON.parse(readFileSync(path, "utf-8"));
  } catch {
    return null;
  }

  if (data.hash !== jobNamesHash(jobNames)) return null;

  const classifications = data.jobs;
  if (typeof classifications !== "object" || classifications === null) return null;

  const result = classifications as Record<string, string>;
  const jobSet = new Set(jobNames);
  const classSet = new Set(Object.keys(result));
  if (jobSet.size !== classSet.size || [...jobSet].some((n) => !classSet.has(n))) return null;

  return result;
}

export function saveCache(
  repoRoot: string,
  jobNames: string[],
  classifications: Record<string, string>,
): void {
  const dir = join(repoRoot, CACHE_DIR);
  mkdirSync(dir, { recursive: true });
  writeFileSync(
    cachePath(repoRoot),
    `${JSON.stringify({ hash: jobNamesHash(jobNames), jobs: classifications }, null, 2)}\n`,
  );
}

const CLASSIFY_PROMPT = `Classify each CI job as either "verify" or "infra".

- **verify**: code quality jobs that make sense to run locally (lint, test, typecheck, build compilation, audit, format, check, coverage, analyze).
- **infra**: infrastructure jobs that should NOT run locally (deploy, docker build/push, publish, release, cleanup, sync, tag, migrate, seed, packaging artifacts like wheel builds, notifications, triggers).

Jobs:
{job_list}

Reply with ONLY a JSON object mapping each job name to "verify" or "infra". No markdown fences, no explanation. Example:
{{"lint": "verify", "test:unit": "verify", "deploy:prod": "infra"}}`;

export async function classifyWithLLM(
  jobNames: string[],
  agent = "claude",
  repoRoot?: string,
): Promise<Record<string, string> | null> {
  const jobList = jobNames.map((n) => `- ${n}`).join("\n");
  const prompt = CLASSIFY_PROMPT.replace("{job_list}", jobList);

  let output: string | null = null;
  if (agent === "claude") output = await callClaude(prompt, repoRoot);
  else if (agent === "codex") output = await callCodex(prompt, repoRoot);
  else return null;

  if (output === null) return null;
  return parseClassification(output, jobNames);
}

async function callClaude(prompt: string, repoRoot?: string): Promise<string | null> {
  const binary = "claude";
  if (!which(binary)) return null;

  try {
    const result = await execa(binary, ["-p", prompt, "--output-format", "text"], {
      cwd: repoRoot,
      timeout: 30_000,
      reject: false,
    });
    if (result.exitCode !== 0) return null;
    return result.stdout.trim();
  } catch {
    return null;
  }
}

async function callCodex(prompt: string, repoRoot?: string): Promise<string | null> {
  const binary = "codex";
  if (!which(binary)) return null;

  try {
    const result = await execa(binary, ["exec", prompt], {
      cwd: repoRoot,
      timeout: 30_000,
      reject: false,
    });
    if (result.exitCode !== 0) return null;
    return result.stdout.trim();
  } catch {
    return null;
  }
}

export function parseClassification(
  raw: string,
  jobNames: string[],
): Record<string, string> | null {
  let text = raw.trim();
  if (text.startsWith("```")) {
    const lines = text.split("\n").filter((ln) => !ln.startsWith("```"));
    text = lines.join("\n").trim();
  }

  let data: unknown;
  try {
    data = JSON.parse(text);
  } catch {
    const start = text.indexOf("{");
    const end = text.lastIndexOf("}");
    if (start === -1 || end === -1) return null;
    try {
      data = JSON.parse(text.slice(start, end + 1));
    } catch {
      return null;
    }
  }

  if (typeof data !== "object" || data === null) return null;

  const parsed = data as Record<string, unknown>;
  const result: Record<string, string> = {};
  for (const name of jobNames) {
    const val = parsed[name];
    result[name] = val === "verify" || val === "infra" ? val : "verify";
  }

  return result;
}

function matchesAllowlist(jobName: string, allowlist: string[]): boolean {
  const separators = [":", "-", "_"];
  for (const entry of allowlist) {
    if (jobName === entry) return true;
    if (!jobName.startsWith(entry) || jobName.length <= entry.length) continue;
    const sep = jobName.charAt(entry.length);
    if (separators.includes(sep)) return true;
  }
  return false;
}

export function applyFilter(
  jobs: CIJob[],
  cfg: FilterConfig,
  classifications: Record<string, string> | null = null,
): CIJob[] {
  return jobs.map((job) => ({
    ...job,
    skipReason: computeSkipReason(job.name, cfg, classifications),
  }));
}

function computeSkipReason(
  name: string,
  cfg: FilterConfig,
  classifications: Record<string, string> | null,
): string | null {
  if (cfg.only !== null && !matchesAllowlist(name, cfg.only)) {
    return `not in --jobs allowlist ${JSON.stringify(cfg.only)}`;
  }
  if (cfg.only === null && classifications) {
    const label = classifications[name] ?? "verify";
    if (label === "infra") return "infrastructure job (classified by LLM)";
  }
  if (cfg.exclude && cfg.exclude.length > 0 && matchesAllowlist(name, cfg.exclude)) {
    return `in exclude list ${JSON.stringify(cfg.exclude)}`;
  }
  return null;
}
