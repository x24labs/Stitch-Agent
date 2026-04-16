import type { FixContext, FixOutcome } from "../core/models.js";

/** Contract that every agent backend (Claude Code, Codex, etc.) must implement. */
export interface AgentDriver {
  name: string;
  onOutput: ((log: string) => void) | null;
  fix(context: FixContext): Promise<FixOutcome>;
}
