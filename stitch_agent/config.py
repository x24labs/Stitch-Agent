from __future__ import annotations

from typing import TYPE_CHECKING

import yaml

from stitch_agent.models import StitchConfig

if TYPE_CHECKING:
    from pathlib import Path

DEFAULT_CONFIG_FILENAME = ".stitch.yml"


def load_config(repo_root: Path) -> StitchConfig:
    config_path = repo_root / DEFAULT_CONFIG_FILENAME
    if not config_path.exists():
        return StitchConfig()

    raw = yaml.safe_load(config_path.read_text())
    if not isinstance(raw, dict):
        return StitchConfig()

    return StitchConfig.model_validate(raw)


def parse_config(raw_yaml: str) -> StitchConfig:
    data = yaml.safe_load(raw_yaml)
    if not isinstance(data, dict):
        return StitchConfig()
    return StitchConfig.model_validate(data)
