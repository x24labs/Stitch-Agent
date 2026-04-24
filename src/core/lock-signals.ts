const releasers = new Set<() => void>();
let installed = false;

function runAll(): void {
  for (const fn of releasers) {
    try {
      fn();
    } catch {
      // swallow
    }
  }
}

function install(): void {
  if (installed) return;
  installed = true;
  process.on("exit", runAll);
  const onSignal = (sig: NodeJS.Signals) => {
    runAll();
    process.exit(sig === "SIGINT" ? 130 : sig === "SIGTERM" ? 143 : 129);
  };
  process.on("SIGINT", () => onSignal("SIGINT"));
  process.on("SIGTERM", () => onSignal("SIGTERM"));
  process.on("SIGHUP", () => onSignal("SIGHUP"));
  process.on("uncaughtException", (err) => {
    runAll();
    throw err;
  });
}

export function registerRelease(fn: () => void): () => void {
  releasers.add(fn);
  install();
  return () => {
    releasers.delete(fn);
  };
}
