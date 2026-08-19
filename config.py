"""Load config.yaml and expose a mutable dict + helpers."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"

_CFG: dict[str, Any] = {}


def _load() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError("config.yaml must be a mapping")
    return data


def reload() -> dict[str, Any]:
    global _CFG
    _CFG = _load()
    return _CFG


def cfg() -> dict[str, Any]:
    if not _CFG:
        reload()
    return _CFG


def get(path: str, default: Any = None) -> Any:
    cur: Any = cfg()
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return default
        cur = cur[part]
    return cur


def data_dir() -> Path:
    p = ROOT / str(get("storage.dir", "data"))
    p.mkdir(parents=True, exist_ok=True)
    (p / "parquet").mkdir(parents=True, exist_ok=True)
    return p


def snapshot() -> dict[str, Any]:
    return deepcopy(cfg())


reload()
