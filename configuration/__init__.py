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

    Rules:
    - Dict vs dict: recurse.
    - List vs list: unique union preserving order (parent items first, then new child items not already present).
    - Other types: override with b's value.
    Inputs are not mutated.
    """
    out: Dict[str, Any] = copy.deepcopy(a) if isinstance(a, dict) else {}
    for k, v in (b or {}).items():
        av = out.get(k)
        if isinstance(av, dict) and isinstance(v, dict):
            out[k] = deep_merge_dicts(av, v)  # type: ignore[index]
        elif isinstance(av, list) and isinstance(v, list):
            out[k] = unique_union(av, v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def unique_union(parent: list[Any], child: list[Any]) -> list[Any]:
    """Return ordered unique union of two lists (parent items first).

    Behavior:
    - Primitive items (str/int/float/bool/None): dedupe by value.
    - Dict items: dedupe by a frozenset of their shallow key/value pairs; if duplicate dict appears, merge with deep_merge_dicts.
    - Other objects: dedupe by id().
    All items are deep-copied; dict duplicates are deep-merged.
    """
    merged_list: list[Any] = []
    seen_map: dict[Any, Any] = {}

    def _key(item: Any) -> Any:
        if isinstance(item, (str, int, float, bool, type(None))):
            return item
        if isinstance(item, dict):
            # Shallow signature; adequate for config structures.
            try:
                return ('dict', frozenset(item.items()))
            except Exception:
                return ('dict', id(item))
        return ('obj', id(item))

    def _add_or_merge(item: Any):
        k = _key(item)
        existing = seen_map.get(k)
        if existing is None:
            # Brand new.
            seen_map[k] = copy.deepcopy(item)
        else:
            # Merge if both dicts; else keep existing (parent precedence).
            if isinstance(existing, dict) and isinstance(item, dict):
                seen_map[k] = deep_merge_dicts(existing, item)

    for it in parent:
        _add_or_merge(it)
    for it in child:
        _add_or_merge(it)

    # Preserve parent-first order; we need to iterate original order again collecting by key.
    emitted: set[Any] = set()
    for it in parent + child:
        k = _key(it)
        if k in emitted:
            continue
        emitted.add(k)
        merged_list.append(copy.deepcopy(seen_map[k]))
    return merged_list


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
