import { existsSync, readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

interface RepoContext {
  languages: string[];
  packageManager: string | null;
  frameworks: string[];
  ciPlatform: string | null;
  hasTestJobs: boolean;
  existingCIFile: string | null;
  entryFiles: string[];
}

const LANG_SIGNALS: [string, string, string | null, string[]][] = [
  ["pyproject.toml", "python", "uv", []],
  ["setup.py", "python", "pip", []],
  ["setup.cfg", "python", "pip", []],
  ["requirements.txt", "python", "pip", []],
  ["Pipfile", "python", "pipenv", []],
  ["package.json", "javascript", null, []],
  ["bun.lockb", "javascript", "bun", []],
  ["bun.lock", "javascript", "bun", []],
  ["pnpm-lock.yaml", "javascript", "pnpm", []],
  ["yarn.lock", "javascript", "yarn", []],
  ["package-lock.json", "javascript", "npm", []],
  ["go.mod", "go", "go", []],
  ["Cargo.toml", "rust", "cargo", []],
  ["Gemfile", "ruby", "bundler", []],
  ["composer.json", "php", "composer", []],
];

const FRAMEWORK_SIGNALS: [string, string][] = [
  ["pytest.ini", "pytest"],
  ["conftest.py", "pytest"],
  ["jest.config.js", "jest"],
  ["jest.config.ts", "jest"],
  ["vitest.config.ts", "vitest"],
  ["vitest.config.js", "vitest"],
  [".eslintrc", "eslint"],
  [".eslintrc.json", "eslint"],
  ["eslint.config.js", "eslint"],
  ["biome.json", "biome"],
  ["biome.jsonc", "biome"],
  [".prettierrc", "prettier"],
  ["tsconfig.json", "typescript"],
  ["ruff.toml", "ruff"],
  ["mypy.ini", "mypy"],
  [".golangci.yml", "golangci-lint"],
  [".golangci.yaml", "golangci-lint"],
];

const CI_CONFIGS: [string, string][] = [
  [".gitlab-ci.yml", "gitlab"],
  [".github/workflows", "github"],
  ["bitbucket-pipelines.yml", "bitbucket"],
  [".circleci/config.yml", "circleci"],
  [".travis.yml", "travis"],
  ["Jenkinsfile", "jenkins"],
  ["azure-pipelines.yml", "azure"],
];

const TEST_JOB_PATTERNS = new Set(["test", "lint", "check", "typecheck", "audit", "format"]);

function detectLanguages(repoRoot: string, ctx: RepoContext): void {
  const seenLangs = new Set<string>();

  // Detect languages and package manager
  for (const [filename, lang, pm] of LANG_SIGNALS) {
    if (existsSync(join(repoRoot, filename))) {
      if (!seenLangs.has(lang)) {
        ctx.languages.push(lang);
        seenLangs.add(lang);
      }
      if (pm && !ctx.packageManager) {
        ctx.packageManager = pm;
      }
    }
  }
}

function detectFrameworks(repoRoot: string, ctx: RepoContext): void {
  // Detect frameworks
  for (const [filename, framework] of FRAMEWORK_SIGNALS) {
    if (existsSync(join(repoRoot, filename)) && !ctx.frameworks.includes(framework)) {
      ctx.frameworks.push(framework);
    }
  }

  // Detect from pyproject.toml
  const pyproject = join(repoRoot, "pyproject.toml");
  if (existsSync(pyproject)) {
    detectFromPyproject(pyproject, ctx);
  }

  // Detect from package.json
  const pkgJson = join(repoRoot, "package.json");
  if (existsSync(pkgJson)) {
    detectFromPackageJson(pkgJson, ctx);
  }
}

function resolveExistingCIFile(full: string, relPath: string): string | null {
  try {
    if (statSync(full).isFile()) return relPath;
    if (statSync(full).isDirectory()) {
      const ymls = readdirSync(full)
        .filter((f) => f.endsWith(".yml") || f.endsWith(".yaml"))
        .sort();
      if (ymls.length > 0) return `${relPath}/${ymls[0]}`;
    }
  } catch {
    // ignore
  }
  return null;
}

function detectCIPlatform(repoRoot: string, ctx: RepoContext): void {
  for (const [path, platform] of CI_CONFIGS) {
    const full = join(repoRoot, path);
    if (!existsSync(full)) continue;
    ctx.ciPlatform = platform;
    ctx.existingCIFile = resolveExistingCIFile(full, path);
    break;
  }
}

function collectEntryFiles(repoRoot: string, ctx: RepoContext): void {
  // Collect key config files
  for (const name of [
    "pyproject.toml",
    "package.json",
    "go.mod",
    "Cargo.toml",
    "Makefile",
    "Dockerfile",
    "docker-compose.yml",
    "tsconfig.json",
    "biome.json",
  ]) {
    if (existsSync(join(repoRoot, name))) {
      ctx.entryFiles.push(name);
    }
  }
}

export function analyzeRepo(repoRoot: string): RepoContext {
  const ctx: RepoContext = {
    languages: [],
    packageManager: null,
    frameworks: [],
    ciPlatform: null,
    hasTestJobs: false,
    existingCIFile: null,
    entryFiles: [],
  };

  detectLanguages(repoRoot, ctx);
  detectFrameworks(repoRoot, ctx);
  detectCIPlatform(repoRoot, ctx);

  // Detect test jobs
  if (ctx.existingCIFile && ctx.ciPlatform) {
    ctx.hasTestJobs = hasTestJobs(repoRoot, ctx.ciPlatform);
  }

  collectEntryFiles(repoRoot, ctx);

  return ctx;
}

export function repoContextSummary(ctx: RepoContext): string {
  const parts: string[] = [];
  if (ctx.languages.length > 0) parts.push(`Languages: ${ctx.languages.join(", ")}`);
  if (ctx.packageManager) parts.push(`Package manager: ${ctx.packageManager}`);
  if (ctx.frameworks.length > 0) parts.push(`Frameworks/tools: ${ctx.frameworks.join(", ")}`);
  if (ctx.ciPlatform) parts.push(`CI platform: ${ctx.ciPlatform}`);
  if (ctx.existingCIFile) parts.push(`Existing CI config: ${ctx.existingCIFile}`);
  parts.push(`Has test jobs: ${ctx.hasTestJobs ? "yes" : "no"}`);
  if (ctx.entryFiles.length > 0) parts.push(`Key files: ${ctx.entryFiles.slice(0, 10).join(", ")}`);
  return parts.join("\n");
}

function detectFromPyproject(path: string, ctx: RepoContext): void {
  let text: string;
  try {
    text = readFileSync(path, "utf-8");
  } catch {
    return;
  }
  if (text.includes("pytest") && !ctx.frameworks.includes("pytest")) ctx.frameworks.push("pytest");
  if (text.includes("ruff") && !ctx.frameworks.includes("ruff")) ctx.frameworks.push("ruff");
  if (text.includes("mypy") && !ctx.frameworks.includes("mypy")) ctx.frameworks.push("mypy");
  if ((text.includes("hatchling") || text.includes("hatch")) && !ctx.packageManager) {
    ctx.packageManager = "hatch";
  }
}

function detectFromPackageJson(path: string, ctx: RepoContext): void {
  let data: Record<string, unknown>;
  try {
    data = JSON.parse(readFileSync(path, "utf-8"));
  } catch {
    return;
  }

  const allDeps: Record<string, unknown> = {
    ...(typeof data.dependencies === "object" && data.dependencies !== null
      ? (data.dependencies as Record<string, unknown>)
      : {}),
    ...(typeof data.devDependencies === "object" && data.devDependencies !== null
      ? (data.devDependencies as Record<string, unknown>)
      : {}),
  };

  const depMap: Record<string, string> = {
    jest: "jest",
    vitest: "vitest",
    mocha: "mocha",
    eslint: "eslint",
    "@biomejs/biome": "biome",
    prettier: "prettier",
    typescript: "typescript",
  };

  for (const [dep, framework] of Object.entries(depMap)) {
    if (dep in allDeps && !ctx.frameworks.includes(framework)) {
      ctx.frameworks.push(framework);
    }
  }

  const scripts =
    typeof data.scripts === "object" && data.scripts !== null
      ? (data.scripts as Record<string, unknown>)
      : {};
  if ("test" in scripts) {
    ctx.entryFiles.push(`package.json scripts.test: ${scripts.test}`);
  }
  if ("lint" in scripts) {
    ctx.entryFiles.push(`package.json scripts.lint: ${scripts.lint}`);
  }
}

function hasTestJobsGitlab(repoRoot: string): boolean {
  const ciFile = join(repoRoot, ".gitlab-ci.yml");
  if (!existsSync(ciFile)) return false;
  try {
    const yaml = require("js-yaml");
    const data = yaml.load(readFileSync(ciFile, "utf-8"));
    if (typeof data !== "object" || data === null) return false;
    for (const key of Object.keys(data as Record<string, unknown>)) {
      if (
        typeof key === "string" &&
        [...TEST_JOB_PATTERNS].some((p) => key.toLowerCase().includes(p))
      ) {
        return true;
      }
    }
  } catch {
    return false;
  }
  return false;
}

function matchesTestPattern(name: string): boolean {
  const lower = name.toLowerCase();
  return [...TEST_JOB_PATTERNS].some((p) => lower.includes(p));
}

function hasTestJobsGithub(repoRoot: string): boolean {
  const wfDir = join(repoRoot, ".github", "workflows");
  if (!existsSync(wfDir)) return false;
  try {
    const yaml = require("js-yaml");
    for (const file of readdirSync(wfDir).filter((f) => f.endsWith(".yml"))) {
      const data = yaml.load(readFileSync(join(wfDir, file), "utf-8"));
      if (typeof data !== "object" || data === null) continue;
      const jobs = (data as Record<string, unknown>).jobs;
      if (typeof jobs !== "object" || jobs === null) continue;
      if (Object.keys(jobs as Record<string, unknown>).some(matchesTestPattern)) return true;
    }
  } catch {
    return false;
  }
  return false;
}

function hasTestJobs(repoRoot: string, platform: string): boolean {
  if (platform === "gitlab") return hasTestJobsGitlab(repoRoot);
  if (platform === "github") return hasTestJobsGithub(repoRoot);
  return false;
}
