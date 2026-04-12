import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import yaml from "js-yaml";
import type { CIJob } from "./models.js";
import type { CIPlatform } from "./ci-detect.js";

export class CIParseError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "CIParseError";
  }
}

const GITLAB_RESERVED_KEYS = new Set([
  "default",
  "image",
  "include",
  "services",
  "stages",
  "variables",
  "workflow",
  "cache",
  "before_script",
  "after_script",
]);

const DEFAULT_GITLAB_STAGES = [".pre", "build", "test", "deploy", ".post"];

export function parseCIConfig(repoRoot: string, platform: CIPlatform = "unknown"): CIJob[] {
  const jobs: CIJob[] = [];

  const parseGitlab = platform === "gitlab" || platform === "unknown";
  const parseGithub = platform === "github" || platform === "unknown";

  if (parseGitlab) {
    const glPath = join(repoRoot, ".gitlab-ci.yml");
    if (existsSync(glPath)) {
      jobs.push(...parseGitlabCI(glPath));
    }
  }

  if (parseGithub) {
    const ghDir = join(repoRoot, ".github", "workflows");
    if (existsSync(ghDir) && statSync(ghDir).isDirectory()) {
      const files = readdirSync(ghDir)
        .filter((f) => f.endsWith(".yml") || f.endsWith(".yaml"))
        .sort();
      for (const file of files) {
        jobs.push(...parseGithubWorkflow(join(ghDir, file)));
      }
    }
  }

  return jobs;
}

function loadYaml(path: string): unknown {
  try {
    const text = readFileSync(path, "utf-8");
    return yaml.load(text);
  } catch (err) {
    if (err instanceof yaml.YAMLException) {
      throw new CIParseError(`Invalid YAML in ${path}: ${err.message}`);
    }
    throw err;
  }
}

function normalizeScript(raw: unknown): string[] {
  if (raw === null || raw === undefined) return [];
  if (typeof raw === "string") return [raw];
  if (Array.isArray(raw)) {
    const out: string[] = [];
    for (const item of raw) {
      if (typeof item === "string") out.push(item);
      else if (Array.isArray(item)) {
        for (const x of item) {
          if (typeof x === "string") out.push(x);
        }
      }
    }
    return out;
  }
  return [];
}

function parseGitlabCI(path: string): CIJob[] {
  const data = loadYaml(path);
  if (typeof data !== "object" || data === null) return [];
  const doc = data as Record<string, unknown>;

  // Stage order
  const stagesRaw = doc.stages;
  let stagesOrder: string[];
  if (Array.isArray(stagesRaw) && stagesRaw.length > 0) {
    stagesOrder = stagesRaw.filter((s): s is string => typeof s === "string");
  } else {
    stagesOrder = [...DEFAULT_GITLAB_STAGES];
  }

  // Default image
  let defaultImage: string | null = null;
  const defaultBlock = doc.default;
  if (typeof defaultBlock === "object" && defaultBlock !== null) {
    const db = defaultBlock as Record<string, unknown>;
    defaultImage = extractImage(db.image);
  }
  if (defaultImage === null) {
    defaultImage = extractImage(doc.image);
  }

  // Top-level before_script
  const topBeforeScript = normalizeScript(doc.before_script);

  // Extract jobs
  const rawJobs: [string, Record<string, unknown>][] = [];
  for (const [key, value] of Object.entries(doc)) {
    if (typeof key !== "string") continue;
    if (key.startsWith(".")) continue;
    if (GITLAB_RESERVED_KEYS.has(key)) continue;
    if (typeof value !== "object" || value === null) continue;
    const block = value as Record<string, unknown>;
    if (!("script" in block) && !("run" in block)) continue;
    rawJobs.push([key, block]);
  }

  // Group by stage
  const byStage = new Map<string, CIJob[]>();
  for (const [name, block] of rawJobs) {
    const stage = typeof block.stage === "string" ? block.stage : "test";
    if (!stagesOrder.includes(stage)) {
      stagesOrder.push(stage);
    }

    let jobImage = defaultImage;
    const imgOverride = extractImage(block.image);
    if (imgOverride !== null) jobImage = imgOverride;

    const beforeScript = normalizeScript(block.before_script);
    const before = beforeScript.length > 0 ? beforeScript : topBeforeScript;

    let script = normalizeScript(block.script);
    if (script.length === 0) script = normalizeScript(block.run);

    const fullScript = [...before, ...script];
    const job: CIJob = {
      name,
      stage,
      script: fullScript,
      image: jobImage,
      sourceFile: path.split("/").pop() ?? "",
      skipReason: null,
    };

    if (!byStage.has(stage)) byStage.set(stage, []);
    byStage.get(stage)!.push(job);
  }

  // Order by stage
  const ordered: CIJob[] = [];
  for (const stage of stagesOrder) {
    const stageJobs = byStage.get(stage);
    if (stageJobs) ordered.push(...stageJobs);
  }
  // Defensive: any stages not in order
  for (const [stage, stageJobs] of byStage) {
    if (!stagesOrder.includes(stage)) ordered.push(...stageJobs);
  }

  return ordered;
}

function parseGithubWorkflow(path: string): CIJob[] {
  const data = loadYaml(path);
  if (typeof data !== "object" || data === null) return [];
  const doc = data as Record<string, unknown>;

  const jobsBlock = doc.jobs;
  if (typeof jobsBlock !== "object" || jobsBlock === null) return [];

  const filename = path.split("/").pop() ?? "";
  const stage = `workflow:${filename}`;
  const result: CIJob[] = [];

  for (const [jobName, jobDef] of Object.entries(jobsBlock as Record<string, unknown>)) {
    if (typeof jobName !== "string" || typeof jobDef !== "object" || jobDef === null) continue;
    const def = jobDef as Record<string, unknown>;

    const steps = def.steps;
    const script: string[] = [];
    if (Array.isArray(steps)) {
      for (const step of steps) {
        if (typeof step !== "object" || step === null) continue;
        const s = step as Record<string, unknown>;
        const run = s.run;
        if (typeof run === "string") script.push(run);
        else if (Array.isArray(run)) {
          for (const x of run) {
            if (typeof x === "string") script.push(x);
          }
        }
      }
    }

    if (script.length === 0) continue;

    let image: string | null = null;
    const container = def.container;
    if (typeof container === "string") image = container;
    else if (typeof container === "object" && container !== null) {
      const c = container as Record<string, unknown>;
      if (typeof c.image === "string") image = c.image;
    }

    result.push({
      name: jobName,
      stage,
      script,
      image,
      sourceFile: filename,
      skipReason: null,
    });
  }

  return result;
}

function extractImage(raw: unknown): string | null {
  if (typeof raw === "string") return raw;
  if (typeof raw === "object" && raw !== null) {
    const r = raw as Record<string, unknown>;
    if (typeof r.name === "string") return r.name;
  }
  return null;
}
