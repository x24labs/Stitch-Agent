import { existsSync, statSync } from "node:fs";
import { join } from "node:path";

export type CIPlatform = "gitlab" | "github" | "unknown";

const ENV_SIGNALS: [string, CIPlatform][] = [
  ["GITLAB_CI", "gitlab"],
  ["GITHUB_ACTIONS", "github"],
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
  let hasGithub = false;
  try {
    const ghDir = join(repoRoot, ".github", "workflows");
    hasGithub = existsSync(ghDir) && statSync(ghDir).isDirectory();
  } catch {
    // ignore
  }

  if (hasGitlab && !hasGithub) return "gitlab";
  if (hasGithub && !hasGitlab) return "github";
  return "unknown";
}
