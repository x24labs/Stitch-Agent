import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { ConfigError, loadConfig } from "../../src/core/config.js";

describe("loadConfig", () => {
  let tmp: string;

  beforeEach(() => {
    tmp = join(tmpdir(), `stitch-config-${Date.now()}-${Math.random().toString(36).slice(2)}`);
    mkdirSync(tmp, { recursive: true });
  });

  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
  });

  it("returns null when no config file exists", () => {
    expect(loadConfig(tmp)).toBeNull();
  });

  it("parses a partial config (just max_attempts)", () => {
    writeFileSync(join(tmp, ".stitch.yml"), "max_attempts: 5\n");
    expect(loadConfig(tmp)).toEqual({ max_attempts: 5 });
  });

  it("parses a full config", () => {
    const full = `
agent: codex
max_attempts: 7
push: false
jobs:
  include: [lint, test]
  exclude: [deploy]
classification: none
`;
    writeFileSync(join(tmp, ".stitch.yml"), full);
    expect(loadConfig(tmp)).toEqual({
      agent: "codex",
      max_attempts: 7,
      push: false,
      jobs: { include: ["lint", "test"], exclude: ["deploy"] },
      classification: "none",
    });
  });

  it("accepts .stitch.yaml as an alternate filename", () => {
    writeFileSync(join(tmp, ".stitch.yaml"), "agent: claude\n");
    expect(loadConfig(tmp)).toEqual({ agent: "claude" });
  });

  it("returns {} for an empty file", () => {
    writeFileSync(join(tmp, ".stitch.yml"), "");
    expect(loadConfig(tmp)).toEqual({});
  });

  it("throws ConfigError on invalid YAML", () => {
    writeFileSync(join(tmp, ".stitch.yml"), "agent: claude\n  bad indent: [1,\n");
    expect(() => loadConfig(tmp)).toThrow(ConfigError);
  });

  it("throws ConfigError with clear message on unknown field", () => {
    writeFileSync(join(tmp, ".stitch.yml"), "agent: claude\nunknown_field: 42\n");
    expect(() => loadConfig(tmp)).toThrow(/unknown_field/i);
  });

  it("throws ConfigError on invalid agent value", () => {
    writeFileSync(join(tmp, ".stitch.yml"), "agent: gpt4\n");
    expect(() => loadConfig(tmp)).toThrow(ConfigError);
  });

  it("throws ConfigError on invalid max_attempts (zero)", () => {
    writeFileSync(join(tmp, ".stitch.yml"), "max_attempts: 0\n");
    expect(() => loadConfig(tmp)).toThrow(ConfigError);
  });

  it("throws ConfigError on invalid classification value", () => {
    writeFileSync(join(tmp, ".stitch.yml"), "classification: heuristic\n");
    expect(() => loadConfig(tmp)).toThrow(ConfigError);
  });
});
