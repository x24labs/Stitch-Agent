import { spawn } from "node:child_process";
import { existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";
import { analyzeRepo, repoContextSummary } from "../core/repo-context.js";
import { which } from "../util.js";

interface GenerateOptions {
  agent: string;
  repo: string;
  output: string;
  dryRun: boolean;
}

const MAX_CONTEXT_CHARS = 8_000;

function buildPrompt(repoRoot: string): [string, string] {
  const ctx = analyzeRepo(repoRoot);
  const summary = repoContextSummary(ctx);

  let ciContent = "";
  if (ctx.existingCIFile) {
    const ciPath = resolve(repoRoot, ctx.existingCIFile);
    if (existsSync(ciPath)) {
      try {
        ciContent = readFileSync(ciPath, "utf-8").slice(0, MAX_CONTEXT_CHARS);
      } catch {
        // ignore
      }
    }
  }

  const configSnippets: string[] = [];
  for (const name of ctx.entryFiles) {
    if (name.startsWith("package.json scripts.")) continue;
    const path = resolve(repoRoot, name);
    if (existsSync(path)) {
      try {
        const content = readFileSync(path, "utf-8").slice(0, 2000);
        configSnippets.push(`### ${name}\n\`\`\`\n${content}\n\`\`\``);
      } catch {
        // ignore
      }
    }
  }

  const platform = ctx.ciPlatform ?? "gitlab";
  const parts: string[] = [
    "Generate CI test/lint/check jobs for this repository.\n",
    "## Repository context\n",
    summary,
    "",
  ];

  if (ciContent) {
    parts.push(
      "## Existing CI configuration\n",
      `\`\`\`yaml\n${ciContent}\n\`\`\`\n`,
      "Add ONLY the missing test/lint/check jobs. Preserve the existing structure, stages, and conventions.\n",
    );
  } else {
    parts.push(
      `There is no CI configuration yet. Generate a complete ${platform} CI file with test, lint, and check stages.\n`,
    );
  }

  if (configSnippets.length > 0) {
    parts.push("\n## Config files for reference\n", ...configSnippets, "");
  }

  parts.push(
    "\n## Instructions\n",
    "- Output ONLY valid YAML for the CI configuration",
    `- Target platform: ${platform}`,
    "- Include jobs for: lint, test, type checking (if applicable)",
    "- Use the project's actual tools and commands (from config files)",
    "- Keep jobs minimal and fast",
    "- Do NOT include deploy, docker, or infrastructure jobs",
    "- If the repo already has test jobs, say so and suggest improvements only",
    `\nWorking directory: ${repoRoot}`,
  );

  return [parts.join("\n"), summary];
}

async function callAgent(
  agent: string,
  prompt: string,
  repoRoot: string,
  timeout: number,
): Promise<string | null> {
  const binary = agent === "codex" ? "codex" : "claude";
  if (!which(binary)) {
    console.error(`Error: ${binary} CLI not found in PATH`);
    return null;
  }

  const args = agent === "codex" ? ["exec", prompt] : ["-p", prompt, "--output-format", "text"];

  return new Promise((resolve) => {
    const proc = spawn(binary, args, {
      cwd: repoRoot,
      stdio: ["ignore", "pipe", "ignore"],
    });

    const chunks: Buffer[] = [];
    let done = false;

    const timer = setTimeout(() => {
      if (!done) {
        done = true;
        proc.kill("SIGKILL");
        console.error(`Error: ${binary} timed out after ${timeout}s`);
        resolve(null);
      }
    }, timeout * 1000);

    proc.stdout?.on("data", (chunk: Buffer) => chunks.push(chunk));

    proc.on("close", (code) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      if (code !== 0) {
        console.error(`Error: ${binary} exited with code ${code}`);
        resolve(null);
        return;
      }
      resolve(Buffer.concat(chunks).toString("utf-8"));
    });

    proc.on("error", (err) => {
      if (done) return;
      done = true;
      clearTimeout(timer);
      console.error(`Error: ${binary} failed: ${err}`);
      resolve(null);
    });
  });
}

export async function runGenerateCommand(opts: GenerateOptions): Promise<number> {
  const repoRoot = resolve(opts.repo);
  if (!existsSync(repoRoot)) {
    console.error(`Error: repo path not found: ${repoRoot}`);
    return 2;
  }

  const [prompt, summary] = buildPrompt(repoRoot);

  if (opts.dryRun) {
    console.log("--- Repository analysis ---");
    console.log(summary);
    console.log("\nDry run: skipping LLM call");
    if (opts.output === "json") {
      const ctx = analyzeRepo(repoRoot);
      console.log(
        JSON.stringify(
          {
            languages: ctx.languages,
            packageManager: ctx.packageManager,
            frameworks: ctx.frameworks,
            ciPlatform: ctx.ciPlatform,
            hasTestJobs: ctx.hasTestJobs,
            existingCIFile: ctx.existingCIFile,
          },
          null,
          2,
        ),
      );
    }
    return 0;
  }

  console.log("--- Repository analysis ---");
  console.log(summary);
  console.log(`\nGenerating CI jobs with ${opts.agent}...\n`);

  const result = await callAgent(opts.agent, prompt, repoRoot, 120);
  if (result === null) return 1;

  if (opts.output === "json") {
    console.log(JSON.stringify({ agent: opts.agent, generated: result }));
  } else {
    console.log("--- Generated CI configuration ---");
    console.log(result.trim());
  }

  return 0;
}
