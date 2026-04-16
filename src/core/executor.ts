import { spawn } from "node:child_process";
import type { CIJob, ExecResult } from "./models.js";

const PKG_MGR_RE = /^\s*(apt-get|apt|yum|dnf|apk)\s/;

function needsSudo(cmd: string): boolean {
  if (process.getuid?.() === 0) return false;
  return PKG_MGR_RE.test(cmd);
}

function prependSudo(cmd: string): string {
  const parts = cmd.split(/(&&|\|\|)/);
  return parts
    .map((part) => {
      const stripped = part.trim();
      if (stripped === "&&" || stripped === "||") return part;
      if (PKG_MGR_RE.test(stripped)) {
        return part.replace(stripped, `sudo ${stripped}`);
      }
      return part;
    })
    .join("");
}

function runShellCommand(
  cmd: string,
  cwd: string,
  env: NodeJS.ProcessEnv,
  timeoutMs: number,
  signal?: AbortSignal,
): Promise<{ stdout: string; exitCode: number; timedOut: boolean; cancelled: boolean }> {
  return new Promise((resolve) => {
    if (signal?.aborted) {
      resolve({ stdout: "", exitCode: -1, timedOut: false, cancelled: true });
      return;
    }

    const proc = spawn("sh", ["-c", cmd], {
      cwd,
      env,
      stdio: ["ignore", "pipe", "pipe"],
      detached: true,
    });
    const chunks: Buffer[] = [];
    let timedOut = false;
    let cancelled = false;
    let done = false;

    const kill = () => {
      const pid = proc.pid;
      if (pid === undefined) {
        proc.kill("SIGKILL");
        return;
      }
      try {
        process.kill(-pid, "SIGKILL");
      } catch {
        proc.kill("SIGKILL");
      }
    };

    const timer = setTimeout(() => {
      if (!done) {
        timedOut = true;
        kill();
      }
    }, timeoutMs);

    const onAbort = () => {
      if (!done) {
        cancelled = true;
        kill();
      }
    };
    signal?.addEventListener("abort", onAbort, { once: true });

    proc.stdout?.on("data", (chunk: Buffer) => chunks.push(chunk));
    proc.stderr?.on("data", (chunk: Buffer) => chunks.push(chunk));

    proc.on("close", (code) => {
      done = true;
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
      let exitCode: number;
      if (cancelled) exitCode = -1;
      else if (timedOut) exitCode = 124;
      else exitCode = code ?? -1;
      resolve({
        stdout: Buffer.concat(chunks).toString("utf-8"),
        exitCode,
        timedOut,
        cancelled,
      });
    });

    proc.on("error", (err) => {
      done = true;
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
      resolve({
        stdout: `Stitch: failed to spawn: ${err}\n`,
        exitCode: 127,
        timedOut: false,
        cancelled: false,
      });
    });
  });
}

export class LocalExecutor {
  repoRoot: string;
  timeoutSeconds: number;

  constructor(repoRoot: string, timeoutSeconds = 300) {
    this.repoRoot = repoRoot;
    this.timeoutSeconds = timeoutSeconds;
  }

  async runJob(job: CIJob, signal?: AbortSignal): Promise<ExecResult> {
    if (job.script.length === 0) {
      return {
        log: "(job has no script commands)",
        exitCode: 0,
        timedOut: false,
        durationSeconds: 0,
      };
    }

    const start = performance.now();
    const logParts: string[] = [];
    const env = { ...process.env, STITCH_RUN: "1" };
    let remaining = this.timeoutSeconds * 1000;

    for (const rawCmd of job.script) {
      if (signal?.aborted) {
        const duration = (performance.now() - start) / 1000;
        return {
          log: logParts.join(""),
          exitCode: -1,
          timedOut: false,
          cancelled: true,
          durationSeconds: duration,
        };
      }

      const cmd = needsSudo(rawCmd) ? prependSudo(rawCmd) : rawCmd;
      const cmdStart = performance.now();
      logParts.push(`$ ${cmd}\n`);

      const result = await runShellCommand(
        cmd,
        this.repoRoot,
        env,
        Math.max(100, remaining),
        signal,
      );
      logParts.push(result.stdout);
      const elapsed = performance.now() - cmdStart;
      remaining -= elapsed;

      if (result.cancelled) {
        const duration = (performance.now() - start) / 1000;
        return {
          log: logParts.join(""),
          exitCode: -1,
          timedOut: false,
          cancelled: true,
          durationSeconds: duration,
        };
      }

      if (result.timedOut) {
        logParts.push(`\nStitch: command timed out after ${this.timeoutSeconds}s\n`);
        const duration = (performance.now() - start) / 1000;
        return { log: logParts.join(""), exitCode: 124, timedOut: true, durationSeconds: duration };
      }

      if (result.exitCode !== 0) {
        const duration = (performance.now() - start) / 1000;
        return {
          log: logParts.join(""),
          exitCode: result.exitCode,
          timedOut: false,
          durationSeconds: duration,
        };
      }

      if (remaining <= 0) {
        logParts.push(`\nStitch: overall job timeout reached (${this.timeoutSeconds}s)\n`);
        const duration = (performance.now() - start) / 1000;
        return { log: logParts.join(""), exitCode: 124, timedOut: true, durationSeconds: duration };
      }
    }

    const duration = (performance.now() - start) / 1000;
    return { log: logParts.join(""), exitCode: 0, timedOut: false, durationSeconds: duration };
  }
}
