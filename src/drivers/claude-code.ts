import { spawn } from "node:child_process";
import type { FixContext, FixOutcome } from "../core/models.js";
import { which } from "../util.js";
import { buildPrompt } from "./prompt.js";
import type { AgentDriver } from "./types.js";

export class ClaudeCodeDriver implements AgentDriver {
  name = "claude";
  timeoutSeconds: number;
  binary: string;
  onOutput: ((log: string) => void) | null = null;

  constructor(binary = "claude", timeoutSeconds = 600) {
    this.binary = binary;
    this.timeoutSeconds = timeoutSeconds;
  }

  async fix(context: FixContext): Promise<FixOutcome> {
    if (!which(this.binary)) {
      return { applied: false, reason: `${this.binary} CLI not found in PATH`, driverLog: "" };
    }

    const prompt = buildPrompt(context);
    return new Promise((resolve) => {
      const proc = spawn(
        this.binary,
        [
          "-p",
          prompt,
          "--permission-mode",
          "acceptEdits",
          "--output-format",
          "stream-json",
          "--verbose",
        ],
        { cwd: context.repoRoot, stdio: ["ignore", "pipe", "pipe"] },
      );

      const activity: string[] = [];
      let resultText = "";
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
            driverLog: activity.join("\n").slice(-2000),
          });
        }
      }, this.timeoutSeconds * 1000);

      let buffer = "";
      proc.stdout?.on("data", (chunk: Buffer) => {
        buffer += chunk.toString("utf-8");
        const lines = buffer.split("\n");
        buffer = lines.pop() ?? "";
        for (const line of lines) {
          if (!line.trim()) continue;
          const event = parseEvent(line.trim());
          if (!event) continue;
          if (event.kind === "text") {
            activity.push(event.content);
            this.emit(activity);
          } else if (event.kind === "tool_use") {
            activity.push(`> ${event.content}`);
            this.emit(activity);
          } else if (event.kind === "tool_result") {
            const preview =
              event.content.length > 200 ? `${event.content.slice(0, 200)}...` : event.content;
            activity.push(`  ${preview}`);
            this.emit(activity);
          } else if (event.kind === "result") {
            resultText = event.content;
          }
        }
      });

      proc.on("close", (code) => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        const log = activity.join("\n");

        if (code !== 0) {
          resolve({
            applied: false,
            reason: `${this.binary} exited ${code}`,
            driverLog: log.slice(-2000),
          });
          return;
        }

        resolve({
          applied: true,
          reason: resultText.slice(0, 200) || `${this.binary} CLI completed`,
          driverLog: log.slice(-2000),
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

  private emit(activity: string[]): void {
    if (this.onOutput) {
      this.onOutput(activity.join("\n"));
    }
  }
}

interface ParsedEvent {
  kind: "text" | "tool_use" | "tool_result" | "result";
  content: string;
}

function parseEvent(line: string): ParsedEvent | null {
  let data: Record<string, unknown>;
  try {
    data = JSON.parse(line);
  } catch {
    return null;
  }

  const eventType = data.type;

  if (eventType === "assistant") {
    const msg = data.message as Record<string, unknown> | undefined;
    const contentBlocks = (msg?.content as unknown[]) ?? [];
    const parts: string[] = [];

    for (const block of contentBlocks) {
      if (typeof block !== "object" || block === null) continue;
      const b = block as Record<string, unknown>;

      if (b.type === "text" && typeof b.text === "string" && b.text) {
        parts.push(b.text);
      } else if (b.type === "tool_use") {
        const toolName = (b.name as string) ?? "tool";
        const input = (b.input as Record<string, unknown>) ?? {};
        const desc = (input.description ?? input.command ?? "") as string;
        if (desc) return { kind: "tool_use", content: `${toolName}: ${desc}` };
        const path = (input.file_path ?? input.path ?? "") as string;
        const pattern = (input.pattern ?? input.query ?? "") as string;
        if (path) return { kind: "tool_use", content: `${toolName}: ${path}` };
        if (pattern) return { kind: "tool_use", content: `${toolName}: ${pattern}` };
        return { kind: "tool_use", content: toolName };
      }
    }
    if (parts.length > 0) return { kind: "text", content: parts.join(" ") };
    return null;
  }

  if (eventType === "user") {
    const msg = data.message as Record<string, unknown> | undefined;
    const contentBlocks = (msg?.content as unknown[]) ?? [];
    for (const block of contentBlocks) {
      if (typeof block !== "object" || block === null) continue;
      const b = block as Record<string, unknown>;
      if (b.type === "tool_result") {
        let resultContent = b.content;
        if (Array.isArray(resultContent)) {
          resultContent = resultContent
            .filter((x): x is Record<string, unknown> => typeof x === "object" && x !== null)
            .map((x) => (x.text as string) ?? "")
            .join(" ");
        }
        if (typeof resultContent === "string" && resultContent.trim()) {
          return { kind: "tool_result", content: resultContent.trim() };
        }
      }
    }
    return null;
  }

  if (eventType === "result") {
    return { kind: "result", content: (data.result as string) ?? "" };
  }

  return null;
}
