"""Analyze a repository to detect language, framework, and CI configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


@dataclass
class RepoContext:
    """Detected repository context for CI generation."""

    languages: list[str] = field(default_factory=list)
    package_manager: str | None = None
    frameworks: list[str] = field(default_factory=list)
    ci_platform: str | None = None
    has_test_jobs: bool = False
    existing_ci_file: str | None = None
    entry_files: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = []
        if self.languages:
            parts.append(f"Languages: {', '.join(self.languages)}")
        if self.package_manager:
            parts.append(f"Package manager: {self.package_manager}")
        if self.frameworks:
            parts.append(f"Frameworks/tools: {', '.join(self.frameworks)}")
        if self.ci_platform:
            parts.append(f"CI platform: {self.ci_platform}")
        if self.existing_ci_file:
            parts.append(f"Existing CI config: {self.existing_ci_file}")
        parts.append(f"Has test jobs: {'yes' if self.has_test_jobs else 'no'}")
        if self.entry_files:
            parts.append(f"Key files: {', '.join(self.entry_files[:10])}")
        return "\n".join(parts)


# Language detection: config file -> (language, package_manager, frameworks)
_LANG_SIGNALS: list[tuple[str, str, str | None, list[str]]] = [
    ("pyproject.toml", "python", "uv", []),
    ("setup.py", "python", "pip", []),
    ("setup.cfg", "python", "pip", []),
    ("requirements.txt", "python", "pip", []),
    ("Pipfile", "python", "pipenv", []),
    ("package.json", "javascript", None, []),  # pm detected separately
    ("bun.lockb", "javascript", "bun", []),
    ("pnpm-lock.yaml", "javascript", "pnpm", []),
    ("yarn.lock", "javascript", "yarn", []),
    ("package-lock.json", "javascript", "npm", []),
    ("go.mod", "go", "go", []),
    ("Cargo.toml", "rust", "cargo", []),
    ("Gemfile", "ruby", "bundler", []),
    ("composer.json", "php", "composer", []),
]

# Framework detection from config files
_FRAMEWORK_SIGNALS: list[tuple[str, str]] = [
    ("pytest.ini", "pytest"),
    ("conftest.py", "pytest"),
    ("jest.config.js", "jest"),
    ("jest.config.ts", "jest"),
    ("vitest.config.ts", "vitest"),
    ("vitest.config.js", "vitest"),
    (".eslintrc", "eslint"),
    (".eslintrc.json", "eslint"),
    ("eslint.config.js", "eslint"),
    ("biome.json", "biome"),
    ("biome.jsonc", "biome"),
    (".prettierrc", "prettier"),
    ("tsconfig.json", "typescript"),
    ("ruff.toml", "ruff"),
    ("mypy.ini", "mypy"),
    (".golangci.yml", "golangci-lint"),
    (".golangci.yaml", "golangci-lint"),
]

# CI platforms
_CI_CONFIGS: list[tuple[str, str]] = [
    (".gitlab-ci.yml", "gitlab"),
    (".github/workflows", "github"),
    ("bitbucket-pipelines.yml", "bitbucket"),
    (".circleci/config.yml", "circleci"),
    (".travis.yml", "travis"),
    ("Jenkinsfile", "jenkins"),
    ("azure-pipelines.yml", "azure"),
]

# Test job name patterns (to detect if test jobs already exist)
_TEST_JOB_PATTERNS = {"test", "lint", "check", "typecheck", "audit", "format"}


def analyze_repo(repo_root: Path) -> RepoContext:
    """Analyze a repository and return detected context."""
    ctx = RepoContext()

    # Detect languages and package manager
    seen_langs: set[str] = set()
    for filename, lang, pm, frameworks in _LANG_SIGNALS:
        if (repo_root / filename).exists():
            if lang not in seen_langs:
                ctx.languages.append(lang)
                seen_langs.add(lang)
            if pm and not ctx.package_manager:
                ctx.package_manager = pm
            ctx.frameworks.extend(f for f in frameworks if f not in ctx.frameworks)

    # Detect frameworks
    for filename, framework in _FRAMEWORK_SIGNALS:
        if (repo_root / filename).exists() and framework not in ctx.frameworks:
            ctx.frameworks.append(framework)

    # Detect from pyproject.toml dependencies
    pyproject = repo_root / "pyproject.toml"
    if pyproject.is_file():
        _detect_from_pyproject(pyproject, ctx)

    # Detect from package.json
    pkg_json = repo_root / "package.json"
    if pkg_json.is_file():
        _detect_from_package_json(pkg_json, ctx)

    # Detect CI platform
    for path, platform in _CI_CONFIGS:
        full = repo_root / path
        if full.exists():
            ctx.ci_platform = platform
            if full.is_file():
                ctx.existing_ci_file = path
            elif full.is_dir():
                # For GitHub workflows, find first yml
                ymls = sorted(full.glob("*.yml")) + sorted(full.glob("*.yaml"))
                if ymls:
                    ctx.existing_ci_file = str(ymls[0].relative_to(repo_root))
            break

    # Detect if test jobs already exist
    if ctx.existing_ci_file and ctx.ci_platform:
        ctx.has_test_jobs = _has_test_jobs(repo_root, ctx.ci_platform)

    # Collect key config files
    for name in [
        "pyproject.toml", "package.json", "go.mod", "Cargo.toml",
        "Makefile", "Dockerfile", "docker-compose.yml",
        "tsconfig.json", "biome.json",
    ]:
        if (repo_root / name).is_file():
            ctx.entry_files.append(name)

    return ctx


def _detect_from_pyproject(path: Path, ctx: RepoContext) -> None:
    """Extract framework info from pyproject.toml (simple text scanning)."""
    try:
        text = path.read_text()
    except OSError:
        return

    if "pytest" in text and "pytest" not in ctx.frameworks:
        ctx.frameworks.append("pytest")
    if "ruff" in text and "ruff" not in ctx.frameworks:
        ctx.frameworks.append("ruff")
    if "mypy" in text and "mypy" not in ctx.frameworks:
        ctx.frameworks.append("mypy")
    if "hatchling" in text or "hatch" in text:
        ctx.package_manager = ctx.package_manager or "hatch"


def _detect_from_package_json(path: Path, ctx: RepoContext) -> None:
    """Extract framework info from package.json."""
    import json

    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return

    all_deps = {}
    for key in ("dependencies", "devDependencies"):
        all_deps.update(data.get(key, {}))

    dep_framework_map = {
        "jest": "jest",
        "vitest": "vitest",
        "mocha": "mocha",
        "eslint": "eslint",
        "@biomejs/biome": "biome",
        "prettier": "prettier",
        "typescript": "typescript",
    }
    for dep, framework in dep_framework_map.items():
        if dep in all_deps and framework not in ctx.frameworks:
            ctx.frameworks.append(framework)

    # Detect scripts
    scripts = data.get("scripts", {})
    if "test" in scripts and "test" not in ctx.frameworks:
        ctx.entry_files.append(f"package.json scripts.test: {scripts['test']}")
    if "lint" in scripts:
        ctx.entry_files.append(f"package.json scripts.lint: {scripts['lint']}")


def _has_test_jobs(repo_root: Path, platform: str) -> bool:
    """Check if the CI config already defines test/lint jobs."""
    import yaml

    if platform == "gitlab":
        ci_file = repo_root / ".gitlab-ci.yml"
        if not ci_file.is_file():
            return False
        try:
            data = yaml.safe_load(ci_file.read_text())
        except yaml.YAMLError:
            return False
        if not isinstance(data, dict):
            return False
        for key in data:
            if isinstance(key, str) and any(p in key.lower() for p in _TEST_JOB_PATTERNS):
                return True
        return False

    if platform == "github":
        wf_dir = repo_root / ".github" / "workflows"
        if not wf_dir.is_dir():
            return False
        for wf in wf_dir.glob("*.yml"):
            try:
                data = yaml.safe_load(wf.read_text())
            except yaml.YAMLError:
                continue
            if not isinstance(data, dict):
                continue
            jobs = data.get("jobs", {})
            if isinstance(jobs, dict):
                for job_name in jobs:
                    if any(p in job_name.lower() for p in _TEST_JOB_PATTERNS):
                        return True
        return False

    return False
