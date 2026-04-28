import { spawn } from "node:child_process";
import type { FixContext, FixOutcome } from "../core/models.js";
import { which } from "../util.js";
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

  async fix(context: FixContext, signal?: AbortSignal): Promise<FixOutcome> {
    if (!which(this.binary)) {
      return { applied: false, reason: `${this.binary} CLI not found in PATH`, driverLog: "" };
    }
    if (signal?.aborted) {
      return { applied: false, reason: `${this.binary} aborted before start`, driverLog: "" };
    }

    const prompt = buildPrompt(context);
    return new Promise((resolve) => {
      const proc = spawn(this.binary, ["exec", prompt], {
        cwd: context.repoRoot,
        stdio: ["ignore", "pipe", "pipe"],
      });

      const chunks: Buffer[] = [];
      let done = false;

      const onAbort = () => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        signal?.removeEventListener("abort", onAbort);
        try {
          proc.kill("SIGKILL");
        } catch {
          // ignore
        }
        resolve({
          applied: false,
          reason: `${this.binary} aborted`,
          driverLog: Buffer.concat(chunks).toString("utf-8").slice(-2000),
        });
      };
      signal?.addEventListener("abort", onAbort, { once: true });

      const timer = setTimeout(() => {
        if (!done) {
          done = true;
          signal?.removeEventListener("abort", onAbort);
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
        signal?.removeEventListener("abort", onAbort);
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
        signal?.removeEventListener("abort", onAbort);
        resolve({
          applied: false,
          reason: `failed to spawn ${this.binary}: ${err}`,
          driverLog: "",
        });
      });
    });
  }
}
