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


def profile_name() -> str | None:
    """Active data profile (PLATFORM_PROFILE=shopify) or None for the default simulated platform."""
    return os.getenv("PLATFORM_PROFILE") or None


def load_profile_yaml(name: str, directory: Path | None = None) -> dict[str, Any]:
    """Load ``name`` or, when a profile is active and ``<stem>_<profile>.yaml`` exists, that file instead.

    A profile file may declare ``base: <file>``: the base is loaded first and the profile's keys are merged over it
    (mappings such as ``tables`` are merged key by key, everything else is replaced).
    """
    d = directory or config_dir()
    prof = profile_name()
    chosen = name
    if prof and (d / name.replace(".yaml", f"_{prof}.yaml")).exists():
        chosen = name.replace(".yaml", f"_{prof}.yaml")
    cfg = load_yaml(chosen, d)
    base = cfg.pop("base", None)
    if not base:
        return cfg
    merged = load_yaml(base, d)
    for key, val in cfg.items():
        if isinstance(val, dict) and isinstance(merged.get(key), dict):
            merged[key].update(val)
        else:
            merged[key] = val
    return merged
