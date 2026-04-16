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
  if (platform === "gitlab" || platform === "unknown") {
    collectGitlab(repoRoot, jobs);
  }
  if (platform === "github" || platform === "unknown") {
    collectGithub(repoRoot, jobs);
  }
  if (platform === "bitbucket" || platform === "unknown") {
    collectBitbucket(repoRoot, jobs);
  }
  return jobs;
}

function collectGitlab(repoRoot: string, jobs: CIJob[]): void {
  const glPath = join(repoRoot, ".gitlab-ci.yml");
  if (existsSync(glPath)) jobs.push(...parseGitlabCI(glPath));
}

function collectGithub(repoRoot: string, jobs: CIJob[]): void {
  const ghDir = join(repoRoot, ".github", "workflows");
  if (!existsSync(ghDir) || !statSync(ghDir).isDirectory()) return;
  const files = readdirSync(ghDir)
    .filter((f) => f.endsWith(".yml") || f.endsWith(".yaml"))
    .sort();
  for (const file of files) {
    jobs.push(...parseGithubWorkflow(join(ghDir, file)));
  }
}

function collectBitbucket(repoRoot: string, jobs: CIJob[]): void {
  const bbPath = join(repoRoot, "bitbucket-pipelines.yml");
  if (existsSync(bbPath)) jobs.push(...parseBitbucketPipelines(bbPath));
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
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item) => {
    if (typeof item === "string") return item;
    if (Array.isArray(item)) return item.filter((x): x is string => typeof x === "string");
    return [];
  });
}

// ── GitLab CI ────────────────────────────────────────────────────────────

function resolveGitlabStages(doc: Record<string, unknown>): string[] {
  const raw = doc.stages;
  if (Array.isArray(raw) && raw.length > 0) {
    return raw.filter((s): s is string => typeof s === "string");
  }
  return [...DEFAULT_GITLAB_STAGES];
}

function resolveGitlabDefaultImage(doc: Record<string, unknown>): string | null {
  const defaultBlock = doc.default;
  if (typeof defaultBlock === "object" && defaultBlock !== null) {
    const img = extractImage((defaultBlock as Record<string, unknown>).image);
    if (img !== null) return img;
  }
  return extractImage(doc.image);
}

function extractGitlabRawJobs(doc: Record<string, unknown>): [string, Record<string, unknown>][] {
  const rawJobs: [string, Record<string, unknown>][] = [];
  for (const [key, value] of Object.entries(doc)) {
    if (key.startsWith(".") || GITLAB_RESERVED_KEYS.has(key)) continue;
    if (typeof value !== "object" || value === null) continue;
    const block = value as Record<string, unknown>;
    if ("script" in block || "run" in block) rawJobs.push([key, block]);
  }
  return rawJobs;
}

function buildGitlabJob(
  name: string,
  block: Record<string, unknown>,
  path: string,
  defaultImage: string | null,
  topBeforeScript: string[],
): CIJob {
  const jobImage = extractImage(block.image) ?? defaultImage;
  const beforeScript = normalizeScript(block.before_script);
  const before = beforeScript.length > 0 ? beforeScript : topBeforeScript;
  let script = normalizeScript(block.script);
  if (script.length === 0) script = normalizeScript(block.run);
  const stage = typeof block.stage === "string" ? block.stage : "test";
  return {
    name,
    stage,
    script: [...before, ...script],
    image: jobImage,
    sourceFile: path.split("/").pop() ?? "",
    skipReason: null,
  };
}

function orderByStage(byStage: Map<string, CIJob[]>, stagesOrder: string[]): CIJob[] {
  const ordered: CIJob[] = [];
  for (const stage of stagesOrder) {
    const jobs = byStage.get(stage);
    if (jobs) ordered.push(...jobs);
  }
  for (const [stage, jobs] of byStage) {
    if (!stagesOrder.includes(stage)) ordered.push(...jobs);
  }
  return ordered;
}

function parseGitlabCI(path: string): CIJob[] {
  const data = loadYaml(path);
  if (typeof data !== "object" || data === null) return [];
  const doc = data as Record<string, unknown>;

  const stagesOrder = resolveGitlabStages(doc);
  const defaultImage = resolveGitlabDefaultImage(doc);
  const topBeforeScript = normalizeScript(doc.before_script);
  const rawJobs = extractGitlabRawJobs(doc);

  const byStage = new Map<string, CIJob[]>();
  for (const [name, block] of rawJobs) {
    const job = buildGitlabJob(name, block, path, defaultImage, topBeforeScript);
    if (!stagesOrder.includes(job.stage)) stagesOrder.push(job.stage);
    if (!byStage.has(job.stage)) byStage.set(job.stage, []);
    byStage.get(job.stage)?.push(job);
  }

  return orderByStage(byStage, stagesOrder);
}

// ── GitHub Actions ───────────────────────────────────────────────────────

function extractGithubSteps(steps: unknown): string[] {
  if (!Array.isArray(steps)) return [];
  const script: string[] = [];
  for (const step of steps) {
    if (typeof step !== "object" || step === null) continue;
    const run = (step as Record<string, unknown>).run;
    if (typeof run === "string") {
      script.push(run);
    } else if (Array.isArray(run)) {
      script.push(...run.filter((x): x is string => typeof x === "string"));
    }
  }
  return script;
}

function extractGithubImage(def: Record<string, unknown>): string | null {
  const container = def.container;
  if (typeof container === "string") return container;
  if (typeof container === "object" && container !== null) {
    const img = (container as Record<string, unknown>).image;
    if (typeof img === "string") return img;
  }
  return null;
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
    if (typeof jobDef !== "object" || jobDef === null) continue;
    const def = jobDef as Record<string, unknown>;
    const script = extractGithubSteps(def.steps);
    if (script.length === 0) continue;
    result.push({
      name: jobName,
      stage,
      script,
      image: extractGithubImage(def),
      sourceFile: filename,
      skipReason: null,
    });
  }

  return result;
}

// ── Bitbucket Pipelines ──────────────────────────────────────────────────

function asRecord(val: unknown): Record<string, unknown> | null {
  if (typeof val === "object" && val !== null) return val as Record<string, unknown>;
  return null;
}

function extractBitbucketStep(entry: Record<string, unknown>): Record<string, unknown> | null {
  if ("step" in entry) return asRecord(entry.step);
  return null;
}

function walkParallelSteps(entry: Record<string, unknown>): Record<string, unknown>[] {
  const par = entry.parallel;
  const steps = Array.isArray(par) ? par : asRecord(par)?.steps;
  if (!Array.isArray(steps)) return [];
  const result: Record<string, unknown>[] = [];
  for (const ps of steps) {
    const rec = asRecord(ps);
    if (rec) {
      const inner = extractBitbucketStep(rec);
      if (inner) result.push(inner);
    }
  }
  return result;
}

function walkStageBlock(
  entry: Record<string, unknown>,
): { label: string; steps: Record<string, unknown>[] } | null {
  const stageBlock = asRecord(entry.stage);
  if (!stageBlock) return null;
  const label = typeof stageBlock.name === "string" ? stageBlock.name : "stage";
  const inner = stageBlock.steps;
  if (!Array.isArray(inner)) return { label, steps: [] };
  const steps: Record<string, unknown>[] = [];
  for (const s of inner) {
    const rec = asRecord(s);
    if (rec) {
      const step = extractBitbucketStep(rec);
      if (step) steps.push(step);
    }
  }
  return { label, steps };
}

interface BitbucketContext {
  defaultImage: string | null;
  filename: string;
  usedNames: Set<string>;
  result: CIJob[];
}

function pushBitbucketStep(
  ctx: BitbucketContext,
  stageLabel: string,
  step: Record<string, unknown>,
  fallbackIdx: number,
): void {
  const script = normalizeScript(step.script);
  if (script.length === 0) return;

  const name = typeof step.name === "string" ? step.name : `step-${fallbackIdx}`;
  let unique = name;
  let suffix = 2;
  while (ctx.usedNames.has(unique)) {
    unique = `${name}-${suffix++}`;
  }
  ctx.usedNames.add(unique);

  ctx.result.push({
    name: unique,
    stage: stageLabel,
    script,
    image: extractImage(step.image) ?? ctx.defaultImage,
    sourceFile: ctx.filename,
    skipReason: null,
  });
}

function collectEntrySteps(
  rec: Record<string, unknown>,
  stageLabel: string,
): { label: string; steps: Record<string, unknown>[] } {
  const direct = extractBitbucketStep(rec);
  if (direct) return { label: stageLabel, steps: [direct] };
  if ("parallel" in rec) return { label: stageLabel, steps: walkParallelSteps(rec) };
  if ("stage" in rec) {
    const block = walkStageBlock(rec);
    if (block) return { label: `${stageLabel}:${block.label}`, steps: block.steps };
  }
  return { label: stageLabel, steps: [] };
}

function walkPipelineList(ctx: BitbucketContext, stageLabel: string, list: unknown): void {
  if (!Array.isArray(list)) return;
  let idx = 0;
  for (const entry of list) {
    const rec = asRecord(entry);
    if (!rec) continue;
    const { label, steps } = collectEntrySteps(rec, stageLabel);
    for (const step of steps) {
      pushBitbucketStep(ctx, label, step, idx++);
    }
  }
}

function parseBitbucketPipelines(path: string): CIJob[] {
  const data = loadYaml(path);
  if (typeof data !== "object" || data === null) return [];
  const doc = data as Record<string, unknown>;

  const pipelines = doc.pipelines;
  if (typeof pipelines !== "object" || pipelines === null) return [];
  const pipelinesBlock = pipelines as Record<string, unknown>;

  const ctx: BitbucketContext = {
    defaultImage: extractImage(doc.image),
    filename: path.split("/").pop() ?? "",
    usedNames: new Set<string>(),
    result: [],
  };

  const simpleKinds = ["default"] as const;
  const keyedKinds = ["branches", "pull-requests", "tags", "custom"] as const;

  for (const kind of simpleKinds) {
    if (kind in pipelinesBlock) walkPipelineList(ctx, kind, pipelinesBlock[kind]);
  }
  for (const kind of keyedKinds) {
    const block = pipelinesBlock[kind];
    if (typeof block !== "object" || block === null) continue;
    for (const [pattern, list] of Object.entries(block as Record<string, unknown>)) {
      walkPipelineList(ctx, `${kind}:${pattern}`, list);
    }
  }

  return ctx.result;
}

// ── Shared ───────────────────────────────────────────────────────────────

function extractImage(raw: unknown): string | null {
  if (typeof raw === "string") return raw;
  if (typeof raw === "object" && raw !== null) {
    const r = raw as Record<string, unknown>;
    if (typeof r.name === "string") return r.name;
  }
  return null;
}
