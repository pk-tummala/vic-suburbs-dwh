"""Config loading for the Victoria Suburbs pipeline.

All transform behaviour is driven by YAML under ``config/``. This module is the single
place that resolves and reads it, so both the local generator/tests and the DLT pipeline
share one source of truth.

Resolution order for the config directory:
1. explicit ``config_dir`` argument,
2. ``VIC_CONFIG_DIR`` environment variable (set by the DLT pipeline configuration),
3. the ``config/`` folder at the repo root (local dev / tests).
"""

from __future__ import annotations

import os
from functools import cache
from pathlib import Path
from typing import Any

import yaml


def _repo_root() -> Path:
    # src/vic_suburbs/common/config.py -> repo root is three parents up from this file's dir
    return Path(__file__).resolve().parents[3]


def resolve_config_dir(config_dir: str | os.PathLike[str] | None = None) -> Path:
    if config_dir is not None:
        return Path(config_dir)
    env = os.environ.get("VIC_CONFIG_DIR")
    if env:
        return Path(env)
    return _repo_root() / "config"


def load_yaml(path: str | os.PathLike[str]) -> dict[str, Any]:
    with open(path, encoding="utf-8") as fh:
        return yaml.safe_load(fh)


@cache
def _cached_yaml(path: str) -> dict[str, Any]:
    return load_yaml(path)


def load_entities(config_dir: str | os.PathLike[str] | None = None) -> list[dict[str, Any]]:
    cfg = resolve_config_dir(config_dir)
    return _cached_yaml(str(cfg / "entities.yaml"))["entities"]


def entity_names(config_dir: str | os.PathLike[str] | None = None) -> list[str]:
    return [e["name"] for e in load_entities(config_dir)]


def load_source(entity: str, config_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    cfg = resolve_config_dir(config_dir)
    return _cached_yaml(str(cfg / "sources" / f"{entity}.yaml"))


def load_schema(entity: str, config_dir: str | os.PathLike[str] | None = None) -> dict[str, Any]:
    cfg = resolve_config_dir(config_dir)
    return _cached_yaml(str(cfg / "schemas" / f"{entity}.yaml"))


def load_dq_rules(
    entity: str, config_dir: str | os.PathLike[str] | None = None
) -> list[dict[str, Any]]:
    cfg = resolve_config_dir(config_dir)
    path = cfg / "dq_rules" / f"{entity}.yaml"
    if not path.exists():
        return []
    return _cached_yaml(str(path))["rules"]


def load_entity_config(
    entity: str, config_dir: str | os.PathLike[str] | None = None
) -> dict[str, Any]:
    """Merge source + schema + dq for one entity into a single dict."""
    manifest = {e["name"]: e for e in load_entities(config_dir)}
    if entity not in manifest:
        raise KeyError(f"Unknown entity '{entity}'. Registered: {sorted(manifest)}")
    return {
        "manifest": manifest[entity],
        "source": load_source(entity, config_dir),
        "schema": load_schema(entity, config_dir),
        "dq_rules": load_dq_rules(entity, config_dir),
    }


def load_pipeline_config(
    env: str, config_dir: str | os.PathLike[str] | None = None
) -> dict[str, Any]:
    cfg = resolve_config_dir(config_dir)
    return _cached_yaml(str(cfg / "pipeline" / f"{env}.yaml"))
