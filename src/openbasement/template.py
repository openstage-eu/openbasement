"""Template loading, validation, and normalization."""

from pathlib import Path
from typing import Any

import yaml


TEMPLATES_DIR = Path(__file__).parent / "templates"

# Defaults applied to every field spec
FIELD_DEFAULTS: dict[str, Any] = {
    "multilingual": False,
    "required": False,
    "cardinality": "one",
    "collect": None,
    "direction": "forward",
    "value_type": None,
    "datatype": None,
    "follow": None,
    "exclude": [],
    "transform": None,
}

# Defaults applied to every relation spec
RELATION_DEFAULTS: dict[str, Any] = {
    "cardinality": "many",
    "direction": "forward",
}


def load_template(source: str | Path | dict) -> dict:
    """Load and normalize a template from various sources.

    Args:
        source: One of:
            - str name of a built-in template (e.g. "eu_procedure")
            - Path to a YAML file
            - dict with template content

    Returns:
        Normalized template dict.
    """
    if isinstance(source, dict):
        raw = source
    elif isinstance(source, Path):
        raw = _load_yaml(source)
    elif isinstance(source, str):
        # Check if it's a file path
        path = Path(source)
        if path.exists() and path.suffix in (".yaml", ".yml"):
            raw = _load_yaml(path)
        else:
            # Try as built-in template name
            raw = _load_builtin(source)
    else:
        raise TypeError(f"Unsupported template source type: {type(source)}")

    return _normalize(raw)


def list_builtin_templates() -> list[str]:
    """List available built-in template names."""
    if not TEMPLATES_DIR.exists():
        return []
    return sorted(
        p.stem
        for p in TEMPLATES_DIR.glob("*.yaml")
    )


def _load_yaml(path: Path) -> dict:
    """Load a YAML file and return its contents."""
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"Template file must contain a YAML mapping, got {type(data)}")
    return data


def _load_builtin(name: str) -> dict:
    """Load a built-in template by name."""
    path = TEMPLATES_DIR / f"{name}.yaml"
    if not path.exists():
        available = list_builtin_templates()
        raise FileNotFoundError(
            f"Built-in template {name!r} not found. "
            f"Available: {available}"
        )
    return _load_yaml(path)


def _normalize(raw: dict) -> dict:
    """Normalize a raw template dict by applying defaults."""
    template = {
        "version": raw.get("version", "1"),
        "prefixes": raw.get("prefixes", {}),
        "languages": _normalize_languages(raw.get("languages", {})),
        "same_as_merge": raw.get("same_as_merge", True),
        "entities": {},
    }

    for entity_name, entity_def in raw.get("entities", {}).items():
        template["entities"][entity_name] = _normalize_entity(entity_def)

    return template


def _normalize_languages(lang_config: dict) -> dict:
    """Normalize language configuration."""
    return {
        "preferred": lang_config.get("preferred", ["en"]),
        "fallback": lang_config.get("fallback", "any"),
    }


def _normalize_entity(entity_def: dict) -> dict:
    """Normalize an entity definition."""
    find = entity_def.get("find", {})
    normalized_find = {
        "type": find.get("type"),
        "include_subclasses": find.get("include_subclasses", False),
    }

    # Normalize fields
    fields = {}
    for field_name, field_def in entity_def.get("fields", {}).items():
        fields[field_name] = _normalize_field(field_def)

    # Normalize relations
    relations = {}
    for rel_name, rel_def in entity_def.get("relations", {}).items():
        relations[rel_name] = _normalize_relation(rel_def)

    return {
        "find": normalized_find,
        "fields": fields,
        "relations": relations,
    }


def _normalize_field(field_def: dict) -> dict:
    """Apply defaults to a field spec."""
    normalized = dict(FIELD_DEFAULTS)
    normalized.update(field_def)

    # Normalize predicate to a list (supports aliasing)
    pred = normalized.get("predicate")
    if isinstance(pred, str):
        normalized["predicate"] = [pred]
    elif isinstance(pred, list):
        normalized["predicate"] = pred
    # None stays None (shouldn't happen for valid templates)

    # Normalize follow sub-spec
    if normalized["follow"] and isinstance(normalized["follow"], dict):
        follow = dict(FIELD_DEFAULTS)
        follow.update(normalized["follow"])
        normalized["follow"] = follow

    return normalized


def _normalize_relation(rel_def: dict) -> dict:
    """Apply defaults to a relation spec."""
    normalized = dict(RELATION_DEFAULTS)
    normalized.update(rel_def)

    # Normalize predicate to a list (supports aliasing)
    pred = normalized.get("predicate")
    if isinstance(pred, str):
        normalized["predicate"] = [pred]
    elif isinstance(pred, list):
        normalized["predicate"] = pred

    # Normalize inverse_predicate to a list
    inv = normalized.get("inverse_predicate", [])
    if isinstance(inv, str):
        normalized["inverse_predicate"] = [inv]
    elif isinstance(inv, list):
        normalized["inverse_predicate"] = inv

    return normalized
