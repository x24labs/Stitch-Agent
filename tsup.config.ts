import { defineConfig } from "tsup";

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
  banner: ({ format }) => {
    if (format === "esm") {
      return { js: "#!/usr/bin/env node" };
    }
    return {};
  },
});
