import { Command } from "commander";
import { runDoctorCommand } from "./commands/doctor.js";
import { runGenerateCommand } from "./commands/generate.js";
import { runHistoryCommand } from "./commands/history.js";
import { runRunCommand } from "./commands/run.js";

const program = new Command();

program
  .name("stitch")
  .description("Run your CI jobs locally. Fix failures with AI.")
  .version("2.0.0");

program
  .command("run")
  .description("Run CI jobs locally with an AI fix loop")
  .argument(
    "[agent]",
    "Which agent to delegate fixes to (claude|codex; falls back to .stitch.yml, then 'claude')",
    (val) => {
      if (!["claude", "codex"].includes(val)) {
        throw new Error(`Invalid agent: ${val}. Valid: claude, codex`);
      }
      return val;
    },
  )
  .option("--repo <path>", "Repository root path", ".")
  .option("--max-attempts <n>", "Maximum fix attempts per job", (v) => Number.parseInt(v, 10), 3)
  .option("--output <format>", "Output format", "text")
  .option("--dry-run", "List runnable jobs without executing them", false)
  .option(
    "--fail-fast",
    "Cancel in-flight jobs as soon as one fails, then let the fix-loop proceed",
    false,
  )
  .option("--jobs <list>", "Comma-separated allowlist of job names to run")
  .option("--no-push", "Commit fixes locally but skip pushing to remote")
  .option("--watch", "Watch mode: re-run on file changes", false)
  .option(
    "--debounce <seconds>",
    "Seconds of quiet before re-running in watch mode",
    (v) => Number.parseFloat(v),
    30,
  )
  .action(async (agent, opts, cmd) => {
    const fromCli = <T>(key: string, val: T): T | undefined => {
      const src = cmd.getOptionValueSource(key);
      return src === "cli" || src === "env" ? val : undefined;
    };
    const code = await runRunCommand({
      agent: agent ?? "",
      repo: opts.repo,
      maxAttempts: fromCli("maxAttempts", opts.maxAttempts),
      output: opts.output,
      dryRun: opts.dryRun,
      failFast: opts.failFast,
      jobs: opts.jobs,
      push: fromCli("push", opts.push),
      watch: opts.watch,
      debounce: opts.debounce,
    });
    process.exit(code);
  });

program
  .command("generate")
  .description("Generate CI test/lint jobs using an AI agent")
  .argument("<agent>", "Which agent to use for generation", (val) => {
    if (!["claude", "codex"].includes(val)) {
      throw new Error(`Invalid agent: ${val}. Valid: claude, codex`);
    }
    return val;
  })
  .option("--repo <path>", "Repository root path", ".")
  .option("--output <format>", "Output format", "text")
  .option("--dry-run", "Analyze repo without calling the LLM", false)
  .action(async (agent, opts) => {
    const code = await runGenerateCommand({ agent, ...opts });
    process.exit(code);
  });

program
  .command("doctor")
  .description("Run environment diagnostics (runtime, CI config, agent CLI, permissions)")
  .option("--repo <path>", "Repository root path", ".")
  .option("--output <format>", "Output format (text|json)", "text")
  .action(async (opts) => {
    const code = await runDoctorCommand({ repo: opts.repo, output: opts.output });
    process.exit(code);
  });

program
  .command("history")
  .description("Show recent run history (compacted by streak)")
  .option("--repo <path>", "Repository root path", ".")
  .option("--job <name>", "Filter to a single job name")
  .option("--limit <n>", "Max finalized entries to show", (v) => Number.parseInt(v, 10), 50)
  .option("--output <format>", "Output format (text|json)", "text")
  .action(async (opts) => {
    const code = await runHistoryCommand({
      repo: opts.repo,
      job: opts.job,
      limit: opts.limit,
      output: opts.output,
    });
    process.exit(code);
  });

export function main(argv?: string[]) {
  program.parse(argv ?? process.argv);
}

main();
