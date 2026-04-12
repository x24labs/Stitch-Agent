import { mkdirSync, rmSync, writeFileSync } from "node:fs";
import { join } from "node:path";
import { tmpdir } from "node:os";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { CIParseError, parseCIConfig } from "../../src/core/ci-parser.js";

describe("parseCIConfig", () => {
  let tmp: string;

  beforeEach(() => {
    tmp = join(tmpdir(), `stitch-parse-${Date.now()}-${Math.random().toString(36).slice(2)}`);
    mkdirSync(tmp, { recursive: true });
  });

  afterEach(() => {
    rmSync(tmp, { recursive: true, force: true });
  });

  describe("GitLab CI", () => {
    it("parses jobs ordered by stage", () => {
      writeFileSync(
        join(tmp, ".gitlab-ci.yml"),
        `
stages:
  - build
  - test

build:wheel:
  stage: build
  script:
    - uv build

lint:
  stage: test
  script:
    - ruff check .
`,
      );
      const jobs = parseCIConfig(tmp);
      expect(jobs).toHaveLength(2);
      expect(jobs[0]!.name).toBe("build:wheel");
      expect(jobs[0]!.stage).toBe("build");
      expect(jobs[1]!.name).toBe("lint");
      expect(jobs[1]!.stage).toBe("test");
    });

    it("ignores reserved keys", () => {
      writeFileSync(
        join(tmp, ".gitlab-ci.yml"),
        `
stages:
  - test
variables:
  FOO: bar
default:
  image: python:3.12
lint:
  stage: test
  script:
    - ruff check .
`,
      );
      const jobs = parseCIConfig(tmp);
      expect(jobs).toHaveLength(1);
      expect(jobs[0]!.name).toBe("lint");
    });

    it("ignores hidden templates", () => {
      writeFileSync(
        join(tmp, ".gitlab-ci.yml"),
        `
.template:
  script:
    - echo template
test:
  script:
    - echo test
`,
      );
      const jobs = parseCIConfig(tmp);
      expect(jobs).toHaveLength(1);
      expect(jobs[0]!.name).toBe("test");
    });

    it("inherits top-level image", () => {
      writeFileSync(
        join(tmp, ".gitlab-ci.yml"),
        `
image: python:3.12
test:
  script:
    - pytest
`,
      );
      const jobs = parseCIConfig(tmp);
      expect(jobs[0]!.image).toBe("python:3.12");
    });

    it("job image overrides top-level", () => {
      writeFileSync(
        join(tmp, ".gitlab-ci.yml"),
        `
image: python:3.12
test:
  image: node:20
  script:
    - npm test
`,
      );
      const jobs = parseCIConfig(tmp);
      expect(jobs[0]!.image).toBe("node:20");
    });

    it("inherits default image", () => {
      writeFileSync(
        join(tmp, ".gitlab-ci.yml"),
        `
default:
  image: python:3.12
test:
  script:
    - pytest
`,
      );
      const jobs = parseCIConfig(tmp);
      expect(jobs[0]!.image).toBe("python:3.12");
    });

    it("merges top-level before_script", () => {
      writeFileSync(
        join(tmp, ".gitlab-ci.yml"),
        `
before_script:
  - echo setup
test:
  script:
    - echo test
`,
      );
      const jobs = parseCIConfig(tmp);
      expect(jobs[0]!.script).toEqual(["echo setup", "echo test"]);
    });

    it("job before_script overrides global", () => {
      writeFileSync(
        join(tmp, ".gitlab-ci.yml"),
        `
before_script:
  - echo global
test:
  before_script:
    - echo local
  script:
    - echo test
`,
      );
      const jobs = parseCIConfig(tmp);
      expect(jobs[0]!.script).toEqual(["echo local", "echo test"]);
    });

    it("skips jobs without script key", () => {
      writeFileSync(
        join(tmp, ".gitlab-ci.yml"),
        `
deploy:
  environment: production
test:
  script:
    - pytest
`,
      );
      const jobs = parseCIConfig(tmp);
      expect(jobs).toHaveLength(1);
      expect(jobs[0]!.name).toBe("test");
    });

    it("uses default stage test when none specified", () => {
      writeFileSync(
        join(tmp, ".gitlab-ci.yml"),
        `
lint:
  script:
    - ruff check .
`,
      );
      const jobs = parseCIConfig(tmp);
      expect(jobs[0]!.stage).toBe("test");
    });

    it("handles image as object with name", () => {
      writeFileSync(
        join(tmp, ".gitlab-ci.yml"),
        `
image:
  name: python:3.12
  entrypoint: [""]
test:
  script:
    - pytest
`,
      );
      const jobs = parseCIConfig(tmp);
      expect(jobs[0]!.image).toBe("python:3.12");
    });
  });

  describe("GitHub Actions", () => {
    it("extracts run commands from steps", () => {
      mkdirSync(join(tmp, ".github", "workflows"), { recursive: true });
      writeFileSync(
        join(tmp, ".github", "workflows", "ci.yml"),
        `
name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: npm install
      - run: npm test
`,
      );
      const jobs = parseCIConfig(tmp);
      expect(jobs).toHaveLength(1);
      expect(jobs[0]!.name).toBe("test");
      expect(jobs[0]!.script).toEqual(["npm install", "npm test"]);
    });

    it("skips jobs with only uses steps", () => {
      mkdirSync(join(tmp, ".github", "workflows"), { recursive: true });
      writeFileSync(
        join(tmp, ".github", "workflows", "ci.yml"),
        `
name: CI
on: push
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: some-action@v1
`,
      );
      const jobs = parseCIConfig(tmp);
      expect(jobs).toHaveLength(0);
    });

    it("extracts container image", () => {
      mkdirSync(join(tmp, ".github", "workflows"), { recursive: true });
      writeFileSync(
        join(tmp, ".github", "workflows", "ci.yml"),
        `
name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    container: node:20
    steps:
      - run: npm test
`,
      );
      const jobs = parseCIConfig(tmp);
      expect(jobs[0]!.image).toBe("node:20");
    });

    it("extracts container image from object", () => {
      mkdirSync(join(tmp, ".github", "workflows"), { recursive: true });
      writeFileSync(
        join(tmp, ".github", "workflows", "ci.yml"),
        `
name: CI
on: push
jobs:
  test:
    runs-on: ubuntu-latest
    container:
      image: node:20
    steps:
      - run: npm test
`,
      );
      const jobs = parseCIConfig(tmp);
      expect(jobs[0]!.image).toBe("node:20");
    });
  });

  describe("platform filtering", () => {
    it("only parses gitlab when platform=gitlab", () => {
      writeFileSync(join(tmp, ".gitlab-ci.yml"), "test:\n  script:\n    - echo gl");
      mkdirSync(join(tmp, ".github", "workflows"), { recursive: true });
      writeFileSync(
        join(tmp, ".github", "workflows", "ci.yml"),
        "name: CI\non: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo gh",
      );
      const jobs = parseCIConfig(tmp, "gitlab");
      expect(jobs).toHaveLength(1);
      expect(jobs[0]!.script).toEqual(["echo gl"]);
    });

    it("only parses github when platform=github", () => {
      writeFileSync(join(tmp, ".gitlab-ci.yml"), "test:\n  script:\n    - echo gl");
      mkdirSync(join(tmp, ".github", "workflows"), { recursive: true });
      writeFileSync(
        join(tmp, ".github", "workflows", "ci.yml"),
        "name: CI\non: push\njobs:\n  test:\n    runs-on: ubuntu-latest\n    steps:\n      - run: echo gh",
      );
      const jobs = parseCIConfig(tmp, "github");
      expect(jobs).toHaveLength(1);
      expect(jobs[0]!.script).toEqual(["echo gh"]);
    });
  });

  describe("error handling", () => {
    it("throws CIParseError on malformed YAML", () => {
      writeFileSync(join(tmp, ".gitlab-ci.yml"), "{ invalid yaml: [");
      expect(() => parseCIConfig(tmp)).toThrow(CIParseError);
    });

    it("returns empty array when no CI config found", () => {
      const jobs = parseCIConfig(tmp);
      expect(jobs).toEqual([]);
    });
  });
});
