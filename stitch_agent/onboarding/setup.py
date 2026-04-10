from __future__ import annotations

import json
import tomllib
from dataclasses import dataclass
from typing import TYPE_CHECKING

import yaml

from stitch_agent.config import DEFAULT_CONFIG_FILENAME, load_config
from stitch_agent.models import StitchConfig
from stitch_agent.onboarding.report import CheckResult, CommandReport, build_command_report

if TYPE_CHECKING:
    from pathlib import Path


@dataclass(slots=True)
class DetectedProfile:
    languages: list[str]
    linter: str | None
    test_runner: str | None
    package_manager: str | None
    ci_provider: str | None


def run_setup(*, repo_root: Path, platform: str) -> CommandReport:
    checks: list[CheckResult] = []
    actions_taken: list[str] = []
    actions_skipped: list[str] = []

    if not repo_root.exists() or not repo_root.is_dir():
        checks.append(
            CheckResult(
                id="repo.root",
                status="fail",
                severity="error",
                message=f"Repository path does not exist: {repo_root}",
                remediation="Pass a valid path with `--repo`",
            )
        )
        return build_command_report(command="setup", checks=checks)

    checks.append(
        CheckResult(
            id="repo.root",
            status="pass",
            severity="info",
            message=f"Repository path exists: {repo_root}",
        )
    )

    profile = _detect_profile(repo_root)
    checks.extend(_profile_checks(profile, requested_platform=platform))

    config_path = repo_root / DEFAULT_CONFIG_FILENAME
    existing_config = load_config(repo_root)
    merged = _merge_config(existing_config, profile)
    rendered = _render_config(merged)

    if config_path.exists():
        current = config_path.read_text()
        if _same_config(current, rendered):
            actions_skipped.append(f"Configuration already up-to-date at {DEFAULT_CONFIG_FILENAME}")
        else:
            config_path.write_text(rendered)
            actions_taken.append(f"Updated {DEFAULT_CONFIG_FILENAME} with detected defaults")
    else:
        config_path.write_text(rendered)
        actions_taken.append(f"Created {DEFAULT_CONFIG_FILENAME} with detected defaults")

    return build_command_report(
        command="setup",
        checks=checks,
        actions_taken=actions_taken,
        actions_skipped=actions_skipped,
        next_steps=[
            f"Run `stitch doctor --repo {repo_root} --platform {platform} --json` to validate readiness"
        ],
    )


def _profile_checks(profile: DetectedProfile, *, requested_platform: str) -> list[CheckResult]:
    checks: list[CheckResult] = []

    if profile.languages:
        checks.append(
            CheckResult(
                id="detect.languages",
                status="pass",
                severity="info",
                message=f"Detected languages: {', '.join(profile.languages)}",
            )
        )
    else:
        checks.append(
            CheckResult(
                id="detect.languages",
                status="warn",
                severity="warning",
                message="Could not infer repository language stack",
                remediation="Manually set `languages` in .stitch.yml if detection is incorrect",
            )
        )

    if profile.linter:
        checks.append(
            CheckResult(
                id="detect.linter",
                status="pass",
                severity="info",
                message=f"Detected linter: {profile.linter}",
            )
        )
    else:
        checks.append(
            CheckResult(
                id="detect.linter",
                status="warn",
                severity="warning",
                message="No linter detected from repository markers",
                remediation="Set `linter` in .stitch.yml if you want lint auto-fixes",
            )
        )

    if profile.test_runner:
        checks.append(
            CheckResult(
                id="detect.test_runner",
                status="pass",
                severity="info",
                message=f"Detected test runner: {profile.test_runner}",
            )
        )
    else:
        checks.append(
            CheckResult(
                id="detect.test_runner",
                status="warn",
                severity="warning",
                message="No test runner detected from repository markers",
                remediation="Set `test_runner` in .stitch.yml for strict validation mode",
            )
        )

    if profile.package_manager:
        checks.append(
            CheckResult(
                id="detect.package_manager",
                status="pass",
                severity="info",
                message=f"Detected package manager: {profile.package_manager}",
            )
        )

    if profile.ci_provider and profile.ci_provider != requested_platform:
        checks.append(
            CheckResult(
                id="detect.ci_provider",
                status="warn",
                severity="warning",
                message=(
                    f"Detected CI provider `{profile.ci_provider}` differs from requested `{requested_platform}`"
                ),
                remediation="Use `--platform` that matches your target CI provider",
            )
        )
    elif profile.ci_provider:
        checks.append(
            CheckResult(
                id="detect.ci_provider",
                status="pass",
                severity="info",
                message=f"Detected CI provider: {profile.ci_provider}",
            )
        )

    return checks


def _detect_profile(repo_root: Path) -> DetectedProfile:
    pyproject = _read_pyproject(repo_root / "pyproject.toml")
    package_json = _read_package_json(repo_root / "package.json")

    languages = _detect_languages(repo_root)
    return DetectedProfile(
        languages=languages,
        linter=_detect_linter(repo_root, pyproject=pyproject),
        test_runner=_detect_test_runner(
            repo_root,
            pyproject=pyproject,
            package_json=package_json,
            languages=languages,
        ),
        package_manager=_detect_package_manager(repo_root, languages=languages),
        ci_provider=_detect_ci_provider(repo_root),
    )


def _detect_languages(repo_root: Path) -> list[str]:
    languages: list[str] = []

    if _exists_any(
        repo_root,
        [
            "pyproject.toml",
            "requirements.txt",
            "setup.py",
            "setup.cfg",
            "poetry.lock",
        ],
    ):
        languages.append("python")

    if (repo_root / "go.mod").exists():
        languages.append("go")

    if (repo_root / "Gemfile").exists():
        languages.append("ruby")

    has_node_markers = _exists_any(
        repo_root,
        [
            "package.json",
            "bun.lockb",
            "pnpm-lock.yaml",
            "yarn.lock",
            "package-lock.json",
        ],
    )
    if has_node_markers:
        if _exists_any(repo_root, ["tsconfig.json", "tsconfig.base.json", "tsconfig.app.json"]):
            languages.append("typescript")
        else:
            languages.append("javascript")

    return languages


def _detect_linter(repo_root: Path, *, pyproject: dict[str, object]) -> str | None:
    if (repo_root / ".ruff.toml").exists() or _toml_has_path(pyproject, "tool", "ruff"):
        return "ruff"

    if _exists_any(
        repo_root,
        [
            "eslint.config.js",
            "eslint.config.mjs",
            "eslint.config.cjs",
            ".eslintrc",
            ".eslintrc.js",
            ".eslintrc.cjs",
            ".eslintrc.json",
            ".eslintrc.yaml",
            ".eslintrc.yml",
        ],
    ):
        return "eslint"

    if _exists_any(repo_root, [".golangci.yml", ".golangci.yaml"]):
        return "golangci-lint"

    return None


def _detect_test_runner(
    repo_root: Path,
    *,
    pyproject: dict[str, object],
    package_json: dict[str, object],
    languages: list[str],
) -> str | None:
    if (
        _exists_any(repo_root, ["pytest.ini", "conftest.py"])
        or _toml_has_path(pyproject, "tool", "pytest")
        or _toml_has_path(pyproject, "tool", "pytest", "ini_options")
        or ("python" in languages and (repo_root / "tests").exists())
    ):
        return "pytest"

    if _exists_any(
        repo_root,
        [
            "vitest.config.ts",
            "vitest.config.js",
            "vitest.config.mts",
            "vitest.config.mjs",
        ],
    ):
        return "vitest"

    deps = {
        **_as_dict(package_json.get("dependencies")),
        **_as_dict(package_json.get("devDependencies")),
    }
    if "vitest" in deps:
        return "vitest"
    if "jest" in deps:
        return "jest"

    if "go" in languages:
        return "go"

    return None


def _detect_package_manager(repo_root: Path, *, languages: list[str]) -> str | None:
    if (repo_root / "bun.lockb").exists():
        return "bun"
    if (repo_root / "pnpm-lock.yaml").exists():
        return "pnpm"
    if (repo_root / "yarn.lock").exists():
        return "yarn"
    if (repo_root / "package-lock.json").exists():
        return "npm"
    if (repo_root / "go.mod").exists():
        return "go"
    if "python" in languages:
        return "pip"
    return None


def _detect_ci_provider(repo_root: Path) -> str | None:
    if (repo_root / ".gitlab-ci.yml").exists():
        return "gitlab"
    if (repo_root / ".github" / "workflows").exists():
        return "github"
    return None


def _merge_config(existing: StitchConfig, profile: DetectedProfile) -> StitchConfig:
    return StitchConfig(
        languages=existing.languages or profile.languages,
        linter=existing.linter or profile.linter,
        test_runner=existing.test_runner or profile.test_runner,
        package_manager=existing.package_manager or profile.package_manager,
        conventions=existing.conventions,
    )


def _render_config(config: StitchConfig) -> str:
    payload: dict[str, object] = {}

    if config.languages:
        payload["languages"] = config.languages
    if config.linter:
        payload["linter"] = config.linter
    if config.test_runner:
        payload["test_runner"] = config.test_runner
    if config.package_manager:
        payload["package_manager"] = config.package_manager
    if config.conventions:
        payload["conventions"] = config.conventions

    rendered = yaml.safe_dump(payload, sort_keys=False)
    if not rendered.endswith("\n"):
        rendered = rendered + "\n"
    return rendered


def _same_config(current: str, rendered: str) -> bool:
    return yaml.safe_load(current) == yaml.safe_load(rendered)


def _read_pyproject(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        return tomllib.loads(path.read_text())
    except (tomllib.TOMLDecodeError, OSError):
        return {}


def _read_package_json(path: Path) -> dict[str, object]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _exists_any(repo_root: Path, candidates: list[str]) -> bool:
    return any((repo_root / candidate).exists() for candidate in candidates)


def _toml_has_path(payload: dict[str, object], *parts: str) -> bool:
    current: object = payload
    for part in parts:
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return True


def _as_dict(value: object) -> dict[str, object]:
    return value if isinstance(value, dict) else {}
