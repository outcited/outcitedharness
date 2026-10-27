"""Minimal JSON Schema 2020-12 validator for the bridge's schemas.

Supports exactly the constructs used by dw.finding.v1 and
dw.approval.gascity-ingest.v1: type, const, enum, required, properties,
additionalProperties:false, pattern, minLength, items, allOf, if/then.
Stdlib only. Full-schema enforcement means every keyword in the schema file
is applied — no field list can drift from the schema.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "schemas"
_cache: dict[str, dict] = {}


class SchemaError(ValueError):
    """Schema violation — .errors lists '<path>: message' strings."""


def load_schema(name: str) -> dict:
    if name not in _cache:
        _cache[name] = json.loads((SCHEMA_DIR / name).read_text())
    return _cache[name]


def _type_ok(value: Any, t: str) -> bool:
    return {
        "object": lambda v: isinstance(v, dict),
        "array": lambda v: isinstance(v, list),
        "string": lambda v: isinstance(v, str),
        "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "boolean": lambda v: isinstance(v, bool),
        "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "null": lambda v: v is None,
    }.get(t, lambda v: True)(value)


def _validate(node: dict, value: Any, path: str, errors: list[str]) -> None:
    if "const" in node and value != node["const"]:
        errors.append(f"{path}: must be const {node['const']!r}")
    if "enum" in node and value not in node["enum"]:
        errors.append(f"{path}: {value!r} not in enum {node['enum']}")
    if "type" in node:
        types = node["type"] if isinstance(node["type"], list) else [node["type"]]
        if not any(_type_ok(value, t) for t in types):
            errors.append(f"{path}: expected type {node['type']}, got {type(value).__name__}")
            return
    if isinstance(value, str):
        if "pattern" in node and not re.search(node["pattern"], value):
            errors.append(f"{path}: {value!r} fails pattern {node['pattern']}")
        if "minLength" in node and len(value) < node["minLength"]:
            errors.append(f"{path}: shorter than minLength {node['minLength']}")
    if isinstance(value, dict):
        for k in node.get("required", []):
            if k not in value:
                errors.append(f"{path}: missing required property {k!r}")
        props = node.get("properties", {})
        if node.get("additionalProperties") is False:
            for k in value:
                if k not in props:
                    errors.append(f"{path}: additional property {k!r} not allowed")
        for k, sub in props.items():
            if k in value:
                _validate(sub, value[k], f"{path}/{k}", errors)
    if isinstance(value, list) and "items" in node:
        for i, item in enumerate(value):
            _validate(node["items"], item, f"{path}[{i}]", errors)
    for sub in node.get("allOf", []):
        _validate(sub, value, path, errors)
    if "if" in node:
        probe: list[str] = []
        _validate(node["if"], value, path, probe)
        branch = "then" if not probe else "else"
        if branch in node:
            _validate(node[branch], value, path, errors)


def validate(instance: Any, schema_name: str) -> list[str]:
    errors: list[str] = []
    _validate(load_schema(schema_name), instance, "$", errors)
    return errors


def validate_or_raise(instance: Any, schema_name: str) -> None:
    errors = validate(instance, schema_name)
    if errors:
        raise SchemaError("; ".join(errors))
