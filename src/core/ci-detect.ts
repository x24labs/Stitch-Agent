import { existsSync, statSync } from "node:fs";
import { join } from "node:path";

export type CIPlatform = "gitlab" | "github" | "bitbucket" | "unknown";

const ENV_SIGNALS: [string, CIPlatform][] = [
  ["GITLAB_CI", "gitlab"],
  ["GITHUB_ACTIONS", "github"],
  ["BITBUCKET_BUILD_NUMBER", "bitbucket"],
];

export function detectPlatform(repoRoot?: string): CIPlatform {
  for (const [envVar, platform] of ENV_SIGNALS) {
    if (process.env[envVar]) return platform;
  }
  if (repoRoot !== undefined) return detectFromFiles(repoRoot);
  return "unknown";
}

function detectFromFiles(repoRoot: string): CIPlatform {
  const hasGitlab = existsSync(join(repoRoot, ".gitlab-ci.yml"));
  const hasBitbucket = existsSync(join(repoRoot, "bitbucket-pipelines.yml"));
  let hasGithub = false;
  try {
    const ghDir = join(repoRoot, ".github", "workflows");
    hasGithub = existsSync(ghDir) && statSync(ghDir).isDirectory();
  } catch {
    // ignore
  }

  const matches = [hasGitlab, hasGithub, hasBitbucket].filter(Boolean).length;
  if (matches !== 1) return "unknown";
  if (hasGitlab) return "gitlab";
  if (hasGithub) return "github";
  if (hasBitbucket) return "bitbucket";
  return "unknown";
}
