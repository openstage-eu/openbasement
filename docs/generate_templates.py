"""Generate docs/template-reference.md from built-in YAML templates."""

import sys
from pathlib import Path

# Add src to path so we can import openbasement
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from openbasement.template import (
    FIELD_DEFAULTS,
    RELATION_DEFAULTS,
    list_builtin_templates,
    load_template,
)

OUTPUT_PATH = Path(__file__).resolve().parent / "template-reference.md"


def shorten_predicate(pred: str) -> str:
    """Shorten a predicate for display."""
    # Already short form like cdm:foo
    if ":" in pred and not pred.startswith("http"):
        return pred
    if "#" in pred:
        return pred.rsplit("#", 1)[1]
    return pred


def format_options(field: dict) -> str:
    """Build a compact options string for non-default field values."""
    tokens = []

    # Check for wildcard
    preds = field.get("predicate", [])
    if any("*" in p for p in preds):
        tokens.append("wildcard")

    if field.get("multilingual") and field["multilingual"] != FIELD_DEFAULTS["multilingual"]:
        tokens.append("multilingual")

    if field.get("cardinality", "one") != FIELD_DEFAULTS["cardinality"]:
        tokens.append("many")

    if field.get("collect") and field["collect"] != FIELD_DEFAULTS["collect"]:
        tokens.append(f"collect: {field['collect']}")

    if field.get("value_type") and field["value_type"] != FIELD_DEFAULTS["value_type"]:
        tokens.append(field["value_type"])

    if field.get("datatype") and field["datatype"] != FIELD_DEFAULTS["datatype"]:
        tokens.append(f"datatype: {shorten_predicate(field['datatype'])}")

    if field.get("follow") and field["follow"] != FIELD_DEFAULTS["follow"]:
        follow = field["follow"]
        if isinstance(follow, dict):
            follow_pred = follow.get("predicate", "")
            if follow_pred:
                if isinstance(follow_pred, list):
                    label = ", ".join(shorten_predicate(p) for p in follow_pred)
                else:
                    label = shorten_predicate(str(follow_pred))
                tokens.append(f"follow: {label}")
        else:
            tokens.append(f"follow: {shorten_predicate(str(follow))}")

    if field.get("transform") and field["transform"] != FIELD_DEFAULTS["transform"]:
        tokens.append(f"transform: {field['transform']}")

    exclude = field.get("exclude", [])
    if exclude and exclude != FIELD_DEFAULTS["exclude"]:
        tokens.append(f"exclude: {len(exclude)} predicates")

    return ", ".join(tokens) if tokens else ""


def format_predicates(preds: list) -> str:
    """Format a list of predicates for a table cell."""
    shortened = [f"`{shorten_predicate(p)}`" for p in preds]
    return ", ".join(shortened)


def main() -> None:
    template_names = list_builtin_templates()
    lines: list[str] = []

    lines.append("# Template Reference")
    lines.append("")
    lines.append(f"Built-in templates: {', '.join(f'**{n}**' for n in template_names)}")
    lines.append("")

    for tpl_name in template_names:
        template = load_template(tpl_name)

        lines.append(f"## {tpl_name}")
        lines.append("")

        for entity_name, entity in template["entities"].items():
            lines.append(f"### {entity_name}")
            lines.append("")

            # Find config
            find = entity["find"]
            find_type = find.get("type", "")
            include_sub = find.get("include_subclasses", False)
            lines.append(
                f"**Find:** type `{find_type}`"
                + (", include subclasses" if include_sub else "")
            )
            lines.append("")

            # Fields table
            fields = entity.get("fields", {})
            if fields:
                lines.append("**Fields:**")
                lines.append("")
                lines.append("| Name | Predicates | Options |")
                lines.append("|:-----|:-----------|:--------|")
                for fname, fspec in fields.items():
                    preds = fspec.get("predicate", [])
                    pred_str = format_predicates(preds)
                    opts = format_options(fspec)
                    lines.append(f"| {fname} | {pred_str} | {opts} |")
                lines.append("")

            # Relations table
            relations = entity.get("relations", {})
            if relations:
                lines.append("**Relations:**")
                lines.append("")
                lines.append("| Name | Predicates | Target | Cardinality | Inverse |")
                lines.append("|:-----|:-----------|:-------|:------------|:--------|")
                for rname, rspec in relations.items():
                    preds = rspec.get("predicate", [])
                    pred_str = format_predicates(preds)
                    target = rspec.get("target_template", "")
                    card = rspec.get("cardinality", RELATION_DEFAULTS["cardinality"])
                    inv_preds = rspec.get("inverse_predicate", [])
                    inv_str = format_predicates(inv_preds) if inv_preds else ""
                    lines.append(f"| {rname} | {pred_str} | {target} | {card} | {inv_str} |")
                lines.append("")

    OUTPUT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
