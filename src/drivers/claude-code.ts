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

  async fix(context: FixContext, signal?: AbortSignal): Promise<FixOutcome> {
    if (!which(this.binary)) {
      return { applied: false, reason: `${this.binary} CLI not found in PATH`, driverLog: "" };
    }
    if (signal?.aborted) {
      return { applied: false, reason: `${this.binary} aborted before start`, driverLog: "" };
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
          driverLog: activity.join("\n").slice(-2000),
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
          resultText = handleStdoutLine(line, activity, resultText, (a) => this.emit(a));
        }
      });

      proc.on("close", (code) => {
        if (done) return;
        done = true;
        clearTimeout(timer);
        signal?.removeEventListener("abort", onAbort);
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
        signal?.removeEventListener("abort", onAbort);
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

export interface ParsedEvent {
  kind: "text" | "tool_use" | "tool_result" | "result";
  content: string;
}

export function handleStdoutLine(
  line: string,
  activity: string[],
  resultText: string,
  emit: (activity: string[]) => void,
): string {
  if (!line.trim()) return resultText;
  const event = parseEvent(line.trim());
  if (!event) return resultText;
  if (event.kind === "text") {
    activity.push(event.content);
    emit(activity);
  } else if (event.kind === "tool_use") {
    activity.push(`> ${event.content}`);
    emit(activity);
  } else if (event.kind === "tool_result") {
    const preview =
      event.content.length > 200 ? `${event.content.slice(0, 200)}...` : event.content;
    activity.push(`  ${preview}`);
    emit(activity);
  } else if (event.kind === "result") {
    return event.content;
  }
  return resultText;
}

function parseToolUseBlock(b: Record<string, unknown>): ParsedEvent {
  const toolName = (b.name as string) ?? "tool";
  const input = (b.input as Record<string, unknown>) ?? {};
  const label =
    (input.description as string) ||
    (input.command as string) ||
    (input.file_path as string) ||
    (input.path as string) ||
    (input.pattern as string) ||
    (input.query as string) ||
    "";
  return { kind: "tool_use", content: label ? `${toolName}: ${label}` : toolName };
}

function parseAssistantEvent(data: Record<string, unknown>): ParsedEvent | null {
  const msg = data.message as Record<string, unknown> | undefined;
  const contentBlocks = (msg?.content as unknown[]) ?? [];
  const parts: string[] = [];

  for (const block of contentBlocks) {
    if (typeof block !== "object" || block === null) continue;
    const b = block as Record<string, unknown>;
    if (b.type === "text" && typeof b.text === "string" && b.text) {
      parts.push(b.text);
    } else if (b.type === "tool_use") {
      return parseToolUseBlock(b);
    }
  }
  if (parts.length > 0) return { kind: "text", content: parts.join(" ") };
  return null;
}

function parseUserEvent(data: Record<string, unknown>): ParsedEvent | null {
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

export function parseEvent(line: string): ParsedEvent | null {
  let data: Record<string, unknown>;
  try {
    data = JSON.parse(line);
  } catch {
    return null;
  }

  const eventType = data.type;

  if (eventType === "assistant") return parseAssistantEvent(data);
  if (eventType === "user") return parseUserEvent(data);
  if (eventType === "result") return { kind: "result", content: (data.result as string) ?? "" };

  return null;
}
