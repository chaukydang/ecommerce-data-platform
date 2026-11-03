from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml


def config_dir() -> Path:
    env = os.getenv("PLATFORM_CONFIG_DIR")
    if env:
        return Path(env)
    # repo layout: <root>/src/ecomplatform/config.py -> <root>/configs
    return Path(__file__).resolve().parents[2] / "configs"


def load_yaml(name: str, directory: Path | None = None) -> dict[str, Any]:
    with open((directory or config_dir()) / name, "r", encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def load_sources(directory: Path | None = None) -> dict[str, Any]:
    return load_yaml("sources.yaml", directory)


def entity_config(sources_cfg: dict, source: str, entity: str) -> dict[str, Any]:
    """Merge defaults + source-level + entity-level settings into one flat dict."""
    src = sources_cfg["sources"][source]
    ent = src["entities"][entity]
    merged = {**sources_cfg.get("defaults", {}), **{k: v for k, v in src.items() if k != "entities"}, **ent}
    merged["source_system"] = source
    merged["entity"] = entity
    return merged
