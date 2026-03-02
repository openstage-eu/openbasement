"""Generate docs/audit.md from tests/audit_report.json."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPORT_PATH = ROOT / "tests" / "audit_report.json"
OUTPUT_PATH = ROOT / "docs" / "audit.md"

# Known URI prefixes for shortening
PREFIXES = [
    ("http://publications.europa.eu/ontology/cdm#", "cdm:"),
    ("http://www.w3.org/2004/02/skos/core#", "skos:"),
    ("http://publications.europa.eu/ontology/annotation#", "ann:"),
    ("http://www.w3.org/1999/02/22-rdf-syntax-ns#", "rdf:"),
    ("http://www.w3.org/2000/01/rdf-schema#", "rdfs:"),
    ("http://www.w3.org/2001/XMLSchema#", "xsd:"),
    ("http://www.w3.org/2002/07/owl#", "owl:"),
]


def shorten_uri(uri: str) -> str:
    """Shorten a full URI using known prefixes."""
    for full, short in PREFIXES:
        if uri.startswith(full):
            return short + uri[len(full):]
    # Fallback: fragment after #
    if "#" in uri:
        return uri.rsplit("#", 1)[1]
    return uri


def fmt_pct(value: float) -> str:
    """Format a float as a percentage string."""
    return f"{value * 100:.1f}%"


def write_predicate_table(lines: list[str], predicates: dict, header: str) -> None:
    """Write a predicate table (uncovered or covered)."""
    if not predicates:
        lines.append(f"*No {header.lower()} predicates.*\n")
        return

    sorted_preds = sorted(predicates.items(), key=lambda x: x[1]["fixtures"], reverse=True)
    lines.append(f"| Predicate | Fixtures | Triples |")
    lines.append(f"|:----------|-------:|-------:|")
    for uri, counts in sorted_preds:
        lines.append(f"| `{shorten_uri(uri)}` | {counts['fixtures']} | {counts['triples']} |")
    lines.append("")


def write_missing_table(lines: list[str], missing_freq: dict) -> None:
    """Write a table for template predicates missing from data."""
    if not missing_freq:
        lines.append("*All template predicates found in data.*\n")
        return

    sorted_missing = sorted(missing_freq.items(), key=lambda x: x[1], reverse=True)
    lines.append("| Predicate | Missing in N fixtures |")
    lines.append("|:----------|-------:|")
    for uri, count in sorted_missing:
        lines.append(f"| `{shorten_uri(uri)}` | {count} |")
    lines.append("")


def main() -> None:
    with open(REPORT_PATH, "r", encoding="utf-8") as f:
        report = json.load(f)

    template_name = report["template"]
    fixture_count = report["fixture_count"]
    errors = report["errors"]
    cov = report["coverage"]

    lines: list[str] = []

    # Header
    lines.append("# Audit Coverage Report")
    lines.append("")
    lines.append(
        f"Template: **{template_name}** | "
        f"Fixtures: **{fixture_count}** | "
        f"Errors: **{errors}**"
    )
    lines.append("")

    # Summary table
    lines.append("## Coverage Summary")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|:-------|------:|")
    lines.append(f"| Mean | {fmt_pct(cov['mean'])} |")
    lines.append(f"| Median | {fmt_pct(cov['median'])} |")
    lines.append(f"| Min | {fmt_pct(cov['min'])} |")
    lines.append(f"| Max | {fmt_pct(cov['max'])} |")
    lines.append(f"| Stdev | {fmt_pct(cov['stdev'])} |")
    lines.append("")

    # Per-entity sections
    for entity_name in ("procedure", "event", "document"):
        entity = report["entities"].get(entity_name)
        if entity is None:
            continue

        uncovered = entity.get("uncovered", {})
        covered = entity.get("covered", {})
        missing_freq = entity.get("missing_frequency", {})

        lines.append(f"## {entity_name.title()}")
        lines.append("")

        total_preds = len(uncovered) + len(covered)
        if total_preds > 0:
            entity_coverage = len(covered) / total_preds
            lines.append(
                f"{len(covered)} covered / {total_preds} total predicates "
                f"({fmt_pct(entity_coverage)})"
            )
        else:
            lines.append("No predicates found.")
        lines.append("")

        lines.append(f"### Uncovered predicates ({len(uncovered)})")
        lines.append("")
        write_predicate_table(lines, uncovered, "uncovered")

        lines.append(f"### Covered predicates ({len(covered)})")
        lines.append("")
        write_predicate_table(lines, covered, "covered")

        if missing_freq:
            lines.append(f"### Missing template predicates ({len(missing_freq)})")
            lines.append("")
            lines.append(
                "Predicates defined in the template but absent from the data."
            )
            lines.append("")
            write_missing_table(lines, missing_freq)

    OUTPUT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
