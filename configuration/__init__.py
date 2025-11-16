"""Configuration helpers for cv renderer.

Provides:
- load_yaml(path): Load YAML file safely.
- deep_merge_dicts(a, b): Recursively merge dictionaries (a <- b).
- resolve_configuration(configs, name): Apply `inherits` chain with deep merge.
- compute_config_hash(name): Short hash identifier for configuration name.
"""

from __future__ import annotations

import copy
from typing import Dict, Any, Set

import xxhash
import yaml


def load_yaml(path: str) -> Dict[str, Any]:
    with open(path, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def deep_merge_dicts(a: Dict[str, Any], b: Dict[str, Any]) -> Dict[str, Any]:
    """Deep-merge two dictionaries (a <- b).

    - Nested dicts are merged recursively.
    - Other value types (including lists) are overridden by b.
    - Returns a new dict; inputs are not mutated.
    """
    out: Dict[str, Any] = copy.deepcopy(a) if isinstance(a, dict) else {}
    for k, v in (b or {}).items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = deep_merge_dicts(out[k], v)  # type: ignore[index]
        else:
            out[k] = copy.deepcopy(v)
    return out


def resolve_configuration(configs: Dict[str, Any], name: str, seen: Set[str] | None = None) -> Dict[str, Any]:
    """Resolve a configuration by applying its parents from `inherits` first, then its own fields.

    Supports `inherits` as a string or a list of configuration names. Detects cycles.
    """
    if seen is None:
        seen = set()
    if name in seen:
        raise ValueError(f"Cyclic configuration inheritance detected at '{name}'")
    if name not in configs:
        raise KeyError(f"Configuration '{name}' not found")
    seen = set(seen)
    seen.add(name)

    current = configs.get(name) or {}
    inherits = current.get('inherits') or []
    if isinstance(inherits, str):
        inherits = [inherits]

    merged: Dict[str, Any] = {}
    for parent in inherits:
        parent_resolved = resolve_configuration(configs, parent, seen)
        merged = deep_merge_dicts(merged, parent_resolved)

    # Apply current on top; drop 'inherits' in the effective config
    current_no_inherits = dict(current)
    current_no_inherits.pop('inherits', None)
    merged = deep_merge_dicts(merged, current_no_inherits)
    return merged


def compute_config_hash(name: str) -> str:
    s = (name or '').encode('utf-8')
    return xxhash.xxh32(s).hexdigest()
