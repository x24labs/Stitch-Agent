import { existsSync, readFileSync } from "node:fs";
import { join } from "node:path";
import yaml from "js-yaml";
import { z } from "zod";

const ConfigSchema = z
  .object({
    agent: z.enum(["claude", "codex"]).optional(),
    max_attempts: z.number().int().positive().optional(),
    push: z.boolean().optional(),
    jobs: z
      .object({
        include: z.array(z.string()).optional(),
        exclude: z.array(z.string()).optional(),
      })
      .strict()
      .optional(),
    classification: z.enum(["llm", "none"]).optional(),
  })
  .strict();

/** User-facing configuration loaded from `.stitch.yml`. All fields optional. */
export type StitchConfig = z.infer<typeof ConfigSchema>;

/** Thrown when `.stitch.yml` exists but cannot be read or parsed. */
export class ConfigError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "ConfigError";
  }
}

const CONFIG_FILENAMES = [".stitch.yml", ".stitch.yaml"];

/** Load and validate `.stitch.yml` from the given repo root. Returns `null` if no config file exists. */
export function loadConfig(repoRoot: string): StitchConfig | null {
  for (const name of CONFIG_FILENAMES) {
    const path = join(repoRoot, name);
    if (!existsSync(path)) continue;

    let raw: string;
    try {
      raw = readFileSync(path, "utf-8");
    } catch (err) {
      throw new ConfigError(`Failed to read ${name}: ${(err as Error).message}`);
    }

    let parsed: unknown;
    try {
      parsed = yaml.load(raw);
    } catch (err) {
      throw new ConfigError(`Invalid YAML in ${name}: ${(err as Error).message}`);
    }

    if (parsed === null || parsed === undefined) return {};

    const result = ConfigSchema.safeParse(parsed);
    if (!result.success) {
      const issues = result.error.issues
        .map((i) => `  - ${i.path.join(".") || "(root)"}: ${i.message}`)
        .join("\n");
      throw new ConfigError(`Invalid ${name}:\n${issues}`);
    }

    return result.data;
  }
  return null;
}
