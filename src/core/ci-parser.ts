import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import yaml from "js-yaml";
import type { CIPlatform } from "./ci-detect.js";
import type { CIJob } from "./models.js";

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
  const parseBitbucket = platform === "bitbucket" || platform === "unknown";

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

  if (parseBitbucket) {
    const bbPath = join(repoRoot, "bitbucket-pipelines.yml");
    if (existsSync(bbPath)) {
      jobs.push(...parseBitbucketPipelines(bbPath));
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
    byStage.get(stage)?.push(job);
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

function parseBitbucketPipelines(path: string): CIJob[] {
  const data = loadYaml(path);
  if (typeof data !== "object" || data === null) return [];
  const doc = data as Record<string, unknown>;

  const defaultImage = extractImage(doc.image);
  const pipelines = doc.pipelines;
  if (typeof pipelines !== "object" || pipelines === null) return [];
  const pipelinesBlock = pipelines as Record<string, unknown>;

  const filename = path.split("/").pop() ?? "";
  const result: CIJob[] = [];
  const usedNames = new Set<string>();

  const pushStep = (stageLabel: string, step: Record<string, unknown>, fallbackIdx: number) => {
    const rawScript = normalizeScript(step.script);
    if (rawScript.length === 0) return;

    let name = typeof step.name === "string" ? step.name : `step-${fallbackIdx}`;
    // Bitbucket step names can repeat across pipelines; disambiguate with stage prefix.
    let unique = name;
    let suffix = 2;
    while (usedNames.has(unique)) {
      unique = `${name}-${suffix++}`;
    }
    usedNames.add(unique);
    name = unique;

    let image = extractImage(step.image);
    if (image === null) image = defaultImage;

    result.push({
      name,
      stage: stageLabel,
      script: rawScript,
      image,
      sourceFile: filename,
      skipReason: null,
    });
  };

  const walkPipelineList = (stageLabel: string, list: unknown) => {
    if (!Array.isArray(list)) return;
    let idx = 0;
    for (const entry of list) {
      if (typeof entry !== "object" || entry === null) continue;
      const e = entry as Record<string, unknown>;

      if ("step" in e && typeof e.step === "object" && e.step !== null) {
        pushStep(stageLabel, e.step as Record<string, unknown>, idx++);
        continue;
      }

      if ("parallel" in e) {
        const par = e.parallel;
        const steps = Array.isArray(par)
          ? par
          : typeof par === "object" && par !== null
            ? (par as Record<string, unknown>).steps
            : null;
        if (Array.isArray(steps)) {
          for (const ps of steps) {
            if (typeof ps === "object" && ps !== null) {
              const pe = ps as Record<string, unknown>;
              if ("step" in pe && typeof pe.step === "object" && pe.step !== null) {
                pushStep(stageLabel, pe.step as Record<string, unknown>, idx++);
              }
            }
          }
        }
        continue;
      }

      if ("stage" in e && typeof e.stage === "object" && e.stage !== null) {
        const stageBlock = e.stage as Record<string, unknown>;
        const stageName =
          typeof stageBlock.name === "string" ? stageBlock.name : `${stageLabel}:stage`;
        const label = `${stageLabel}:${stageName}`;
        const inner = stageBlock.steps;
        if (Array.isArray(inner)) {
          for (const s of inner) {
            if (typeof s === "object" && s !== null) {
              const se = s as Record<string, unknown>;
              if ("step" in se && typeof se.step === "object" && se.step !== null) {
                pushStep(label, se.step as Record<string, unknown>, idx++);
              }
            }
          }
        }
      }
    }
  };

  const simpleKinds = ["default"] as const;
  const keyedKinds = ["branches", "pull-requests", "tags", "custom"] as const;

  for (const kind of simpleKinds) {
    if (kind in pipelinesBlock) {
      walkPipelineList(kind, pipelinesBlock[kind]);
    }
  }

  for (const kind of keyedKinds) {
    const block = pipelinesBlock[kind];
    if (typeof block !== "object" || block === null) continue;
    for (const [pattern, list] of Object.entries(block as Record<string, unknown>)) {
      walkPipelineList(`${kind}:${pattern}`, list);
    }
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
