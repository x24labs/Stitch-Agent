import { mkdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { applyFilter, loadCache, saveCache } from "../../src/core/filter.js";
import type { CIJob } from "../../src/core/models.js";

function job(name: string): CIJob {
  return { name, stage: "test", script: ["echo"], image: null, sourceFile: "", skipReason: null };
}

describe("filter cache", () => {
  let tmp: string;

  beforeEach(() => {
    tmp = join(tmpdir(), `stitch-filter-${Date.now()}-${Math.random().toString(36).slice(2)}`);
    mkdirSync(tmp, { recursive: true });
  });

  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
  });

  it("returns null when cache file missing", () => {
    expect(loadCache(tmp, ["lint"])).toBeNull();
  });

  it("returns cached classifications on hash match", () => {
    const names = ["lint", "test"];
    const classifications = { lint: "verify", test: "verify" };
    saveCache(tmp, names, classifications);

    const loaded = loadCache(tmp, names);
    expect(loaded).toEqual(classifications);
  });

  it("returns null on hash mismatch (new job added)", () => {
    saveCache(tmp, ["lint"], { lint: "verify" });
    expect(loadCache(tmp, ["lint", "test"])).toBeNull();
  });

  it("returns null on corrupted JSON", () => {
    mkdirSync(join(tmp, ".stitch"), { recursive: true });
    writeFileSync(join(tmp, ".stitch", "jobs.json"), "not json");
    expect(loadCache(tmp, ["lint"])).toBeNull();
  });

  it("creates .stitch directory if missing", () => {
    saveCache(tmp, ["lint"], { lint: "verify" });
    const content = readFileSync(join(tmp, ".stitch", "jobs.json"), "utf-8");
    expect(JSON.parse(content).jobs.lint).toBe("verify");
  });
});

describe("applyFilter", () => {
  it("skips infra jobs when classifications provided", () => {
    const jobs = [job("lint"), job("deploy")];
    const result = applyFilter(jobs, { only: null }, { lint: "verify", deploy: "infra" });
    expect(result[0]?.skipReason).toBeNull();
    expect(result[1]?.skipReason).toContain("infrastructure");
  });

  it("runs all jobs when no filter and no classifications", () => {
    const jobs = [job("lint"), job("deploy")];
    const result = applyFilter(jobs, { only: null });
    expect(result.every((j) => j.skipReason === null)).toBe(true);
  });

  it("filters by exact match allowlist", () => {
    const jobs = [job("lint"), job("test"), job("deploy")];
    const result = applyFilter(jobs, { only: ["lint"] });
    expect(result[0]?.skipReason).toBeNull();
    expect(result[1]?.skipReason).toContain("allowlist");
    expect(result[2]?.skipReason).toContain("allowlist");
  });

  it("filters by prefix match with separators", () => {
    const jobs = [job("test:unit"), job("test:e2e"), job("lint")];
    const result = applyFilter(jobs, { only: ["test"] });
    expect(result[0]?.skipReason).toBeNull();
    expect(result[1]?.skipReason).toBeNull();
    expect(result[2]?.skipReason).toContain("allowlist");
  });

  it("prefix match requires separator", () => {
    const jobs = [job("testing"), job("test")];
    const result = applyFilter(jobs, { only: ["test"] });
    // "testing" does NOT match "test" prefix (no separator after)
    expect(result[0]?.skipReason).toContain("allowlist");
    expect(result[1]?.skipReason).toBeNull();
  });

  it("allowlist takes precedence over classifications", () => {
    const jobs = [job("lint"), job("deploy")];
    const result = applyFilter(jobs, { only: ["deploy"] }, { lint: "verify", deploy: "infra" });
    // When allowlist is set, classifications are ignored
    expect(result[0]?.skipReason).toContain("allowlist");
    expect(result[1]?.skipReason).toBeNull();
  });

  it("defaults to verify for unknown jobs in classification", () => {
    const jobs = [job("mystery")];
    const result = applyFilter(jobs, { only: null }, { other: "infra" });
    expect(result[0]?.skipReason).toBeNull();
  });

  it("skips jobs matching the exclude list", () => {
    const jobs = [job("lint"), job("deploy:prod"), job("publish")];
    const result = applyFilter(jobs, { only: null, exclude: ["deploy", "publish"] });
    expect(result[0]?.skipReason).toBeNull();
    expect(result[1]?.skipReason).toContain("exclude");
    expect(result[2]?.skipReason).toContain("exclude");
  });

  it("exclude runs after allowlist and does not override it", () => {
    const jobs = [job("lint"), job("deploy")];
    const result = applyFilter(jobs, { only: ["lint"], exclude: ["lint"] });
    // allowlist admitted lint, exclude then skips it
    expect(result[0]?.skipReason).toContain("exclude");
    expect(result[1]?.skipReason).toContain("allowlist");
  });
});
