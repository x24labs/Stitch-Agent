import { Command } from "commander";
import { runGenerateCommand } from "./commands/generate.js";
import { runRunCommand } from "./commands/run.js";

const program = new Command();

program
  .name("stitch")
  .description("Run your CI jobs locally. Fix failures with AI.")
  .version("2.0.0");

program
  .command("run")
  .description("Run CI jobs locally with an AI fix loop")
  .argument("[agent]", "Which agent to delegate fixes to (claude|codex; falls back to .stitch.yml, then 'claude')", (val) => {
    if (!["claude", "codex"].includes(val)) {
      throw new Error(`Invalid agent: ${val}. Valid: claude, codex`);
    }
    return val;
  })
  .option("--repo <path>", "Repository root path", ".")
  .option("--max-attempts <n>", "Maximum fix attempts per job", (v) => Number.parseInt(v, 10), 3)
  .option("--output <format>", "Output format", "text")
  .option("--dry-run", "List runnable jobs without executing them", false)
  .option("--fail-fast", "Stop after the first escalated job", false)
  .option("--jobs <list>", "Comma-separated allowlist of job names to run")
  .option("--no-push", "Commit fixes locally but skip pushing to remote")
  .option("--watch", "Watch mode: re-run on file changes, no fixes", false)
  .option(
    "--debounce <seconds>",
    "Seconds of quiet before re-running in watch mode",
    (v) => Number.parseFloat(v),
    3.0,
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

export function main(argv?: string[]) {
  program.parse(argv ?? process.argv);
}

main();
