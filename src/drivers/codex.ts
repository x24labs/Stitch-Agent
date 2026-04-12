import { spawn } from "node:child_process";
import { which } from "../util.js";
import type { FixContext, FixOutcome } from "../core/models.js";
import { buildPrompt } from "./prompt.js";
import type { AgentDriver } from "./types.js";

export class CodexDriver implements AgentDriver {
  name = "codex";
  timeoutSeconds: number;
  binary: string;
  onOutput: ((log: string) => void) | null = null;

  constructor(binary = "codex", timeoutSeconds = 600) {
    this.binary = binary;
    this.timeoutSeconds = timeoutSeconds;
  }

  async fix(context: FixContext): Promise<FixOutcome> {
    if (!which(this.binary)) {
      return { applied: false, reason: `${this.binary} CLI not found in PATH`, driverLog: "" };
    }

    const prompt = buildPrompt(context);
    return new Promise((resolve) => {
      const proc = spawn(this.binary, ["exec", prompt], {
        cwd: context.repoRoot,
        stdio: ["ignore", "pipe", "pipe"],
      });

      const chunks: Buffer[] = [];
      let done = false;

      const timer = setTimeout(() => {
        if (!done) {
          done = true;
          try {
            proc.kill("SIGKILL");
          } catch {
            // ignore
          }
          resolve({
            applied: false,
            reason: `${this.binary} CLI timeout after ${this.timeoutSeconds}s`,
            driverLog: "",
          });
        }
      }, this.timeoutSeconds * 1000);

      proc.stdout?.on("data", (chunk: Buffer) => chunks.push(chunk));
      proc.stderr?.on("data", (chunk: Buffer) => chunks.push(chunk));

      proc.on("close", (code) => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        const log = Buffer.concat(chunks).toString("utf-8");
        const logTail = log.slice(-2000);

        if (code !== 0) {
          resolve({
            applied: false,
            reason: `${this.binary} exited ${code}`,
            driverLog: logTail,
          });
          return;
        }

        resolve({
          applied: true,
          reason: `${this.binary} CLI completed`,
          driverLog: logTail,
        });
      });

      proc.on("error", (err) => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        resolve({
          applied: false,
          reason: `failed to spawn ${this.binary}: ${err}`,
          driverLog: "",
        });
      });
    });
  }
}
