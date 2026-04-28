import { existsSync, readFileSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const GITIGNORE_FILE = ".gitignore";
const STITCH_ENTRIES = [".stitch/", ".stitch.lock"] as const;
const SECTION_HEADER = "# Stitch (auto-added)";

interface EnsureResult {
  added: string[];
  created: boolean;
}

function isCovered(lines: string[], entry: string): boolean {
  const bare = entry.replace(/\/$/, "");
  const accepted = new Set([entry, bare, `${bare}/`, `${bare}/*`, `${bare}/**`]);
  for (const raw of lines) {
    const line = raw.trim();
    if (!line || line.startsWith("#") || line.startsWith("!")) continue;
    if (accepted.has(line)) return true;
  }
  return false;
}

export function ensureStitchIgnored(repoRoot: string): EnsureResult {
  const path = join(repoRoot, GITIGNORE_FILE);
  const exists = existsSync(path);
  const original = exists ? readFileSync(path, "utf-8") : "";
  const lines = original.split("\n");

  const missing = STITCH_ENTRIES.filter((entry) => !isCovered(lines, entry));
  if (missing.length === 0) {
    return { added: [], created: false };
  }

  const trimmedTail = original.replace(/\n+$/, "");
  const prefix = trimmedTail.length > 0 ? `${trimmedTail}\n\n` : "";
  const block = `${SECTION_HEADER}\n${missing.join("\n")}\n`;
  writeFileSync(path, `${prefix}${block}`);

  return { added: [...missing], created: !exists };
}
