import type { FixContext } from "../core/models.js";

const MAX_LOG_TAIL_CHARS = 12_000;

export function buildBatchPrompt(contexts: FixContext[]): string {
  const perJobChars = Math.floor(MAX_LOG_TAIL_CHARS / contexts.length);
  const parts: string[] = [
    "Multiple local CI jobs failed. Fix ALL of them so every command passes.\n\n## Failing jobs\n\n",
  ];

  for (let i = 0; i < contexts.length; i++) {
    const ctx = contexts[i]!;
    const logTail = ctx.errorLog.slice(-perJobChars);
    parts.push(
      `### ${i + 1}. ${ctx.jobName}\nCommand: ${ctx.command}\n\`\`\`\n${logTail}\n\`\`\`\n\n`,
    );
  }

  parts.push(
    `## Instructions\n- The failures above may share a common root cause, look for that first\n- Fix only what's needed to make ALL commands pass\n- Do not break other passing tests\n- If a failure requires environment changes and cannot be fixed in code, say so explicitly\n- When you believe all fixes are complete, stop and explain what you changed\n\nWorking directory: ${contexts[0]?.repoRoot}\n`,
  );

  return parts.join("");
}

export function buildPrompt(context: FixContext): string {
  if (context.promptOverride !== null) {
    return context.promptOverride;
  }

  const logTail = context.errorLog.slice(-MAX_LOG_TAIL_CHARS);
  return `A local CI job failed. Fix it so the command passes.\n\n## Job\nName: ${context.jobName}\nCommand: ${context.command}\nAttempt: ${context.attempt}\n\n## Error output\n\`\`\`\n${logTail}\n\`\`\`\n\n## Instructions\n- Investigate by reading the relevant files\n- Fix only what's needed to make the command pass\n- Do not break other passing tests\n- If this failure requires environment changes (missing system package, external service) and cannot be fixed in code, say so explicitly and do not modify code\n- When you believe the fix is complete, stop and explain what you changed\n\nWorking directory: ${context.repoRoot}\n`;
}
