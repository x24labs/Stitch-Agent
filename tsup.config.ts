import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { defineConfig } from "tsup";

const pkg = JSON.parse(
  readFileSync(fileURLToPath(new URL("./package.json", import.meta.url)), "utf-8"),
) as { version: string };

export default defineConfig({
  entry: {
    cli: "src/cli.ts",
    index: "src/index.ts",
  },
  format: ["esm"],
  target: "node20",
  clean: true,
  dts: { entry: "src/index.ts" },
  shims: true,
  define: {
    __STITCH_VERSION__: JSON.stringify(pkg.version),
  },
  banner: ({ format }) => {
    if (format === "esm") {
      return { js: "#!/usr/bin/env bun" };
    }
    return {};
  },
});
