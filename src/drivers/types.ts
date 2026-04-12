import type { FixContext, FixOutcome } from "../core/models.js";

export interface AgentDriver {
  name: string;
  onOutput: ((log: string) => void) | null;
  fix(context: FixContext): Promise<FixOutcome>;
}
