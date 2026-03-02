"""Generate docs/audit.md by running audit() on all RDF fixtures directly.

This runs as part of `make docs` so the audit report always reflects the
current code and templates, with no stale intermediate JSON.
"""

import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from rdflib import Graph

from openbasement import audit, load_template

ROOT = Path(__file__).resolve().parent.parent
FIXTURE_DIR = ROOT / "tests" / "fixtures" / "procedures"
OUTPUT_PATH = ROOT / "docs" / "audit.md"
TEMPLATE_NAME = "eu_procedure"

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
    if "#" in uri:
        return uri.rsplit("#", 1)[1]
    return uri


def fmt_pct(value: float) -> str:
    """Format a float as a percentage string."""
    return f"{value * 100:.1f}%"


def get_git_short_hash() -> str:
    """Return the short git commit hash, or 'unknown' if not in a repo."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=ROOT,
            text=True,
        ).strip()
    except Exception:
        return "unknown"


def write_predicate_table(lines: list[str], predicates: dict, header: str) -> None:
    """Write a predicate table (uncovered or covered)."""
    if not predicates:
        lines.append(f"*No {header.lower()} predicates.*\n")
        return

    sorted_preds = sorted(predicates.items(), key=lambda x: x[1]["fixtures"], reverse=True)
    lines.append("| Predicate | Fixtures | Triples |")
    lines.append("|:----------|-------:|-------:|")
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


def run_audit() -> dict:
    """Run audit() on all fixtures and return an aggregated report dict.

    Uses the same accumulation logic as tests/run_audit.py.
    """
    template = load_template(TEMPLATE_NAME)
    rdf_files = sorted(FIXTURE_DIR.glob("*.rdf"))

    if not rdf_files:
        print(f"No .rdf files found in {FIXTURE_DIR}", file=sys.stderr)
        sys.exit(1)

    print(f"Auditing {len(rdf_files)} fixtures with template '{TEMPLATE_NAME}'...")

    uncovered_counts: dict[str, dict[str, dict]] = {}
    covered_counts: dict[str, dict[str, dict]] = {}
    missing_counts: dict[str, dict[str, int]] = {}
    template_predicates: dict[str, set[str]] = {}

    coverages: list[float] = []
    errors = 0
    t0 = time.time()

    for i, rdf_file in enumerate(rdf_files, 1):
        if i % 100 == 0 or i == len(rdf_files):
            elapsed = time.time() - t0
            print(f"  [{i}/{len(rdf_files)}] {elapsed:.1f}s elapsed")

        try:
            g = Graph()
            g.parse(rdf_file, format="xml")
            result = audit(g, template)
        except Exception as e:
            print(f"  ERROR parsing {rdf_file.name}: {e}", file=sys.stderr)
            errors += 1
            continue

        coverages.append(result["summary"]["coverage"])
        fixture_id = rdf_file.stem

        for entity_name, entity_report in result["entities"].items():
            if entity_name not in uncovered_counts:
                uncovered_counts[entity_name] = {}
                covered_counts[entity_name] = {}
                missing_counts[entity_name] = {}
                template_predicates[entity_name] = set()

            for pred, count in entity_report["uncovered"].items():
                if pred not in uncovered_counts[entity_name]:
                    uncovered_counts[entity_name][pred] = {"fixtures": set(), "triples": 0}
                uncovered_counts[entity_name][pred]["fixtures"].add(fixture_id)
                uncovered_counts[entity_name][pred]["triples"] += count

            for pred, count in entity_report["covered"].items():
                if pred not in covered_counts[entity_name]:
                    covered_counts[entity_name][pred] = {"fixtures": set(), "triples": 0}
                covered_counts[entity_name][pred]["fixtures"].add(fixture_id)
                covered_counts[entity_name][pred]["triples"] += count

            for pred in entity_report["missing"]:
                template_predicates[entity_name].add(pred)
                missing_counts[entity_name][pred] = missing_counts[entity_name].get(pred, 0) + 1

            for pred in entity_report["covered"]:
                template_predicates[entity_name].add(pred)

    fixture_count = len(rdf_files) - errors

    report = {
        "template": TEMPLATE_NAME,
        "fixture_count": fixture_count,
        "errors": errors,
        "coverage": {
            "mean": round(statistics.mean(coverages), 4) if coverages else 0,
            "median": round(statistics.median(coverages), 4) if coverages else 0,
            "min": round(min(coverages), 4) if coverages else 0,
            "max": round(max(coverages), 4) if coverages else 0,
            "stdev": round(statistics.stdev(coverages), 4) if len(coverages) > 1 else 0,
        },
        "entities": {},
    }

    for entity_name in sorted(uncovered_counts.keys()):
        uncovered = {
            pred: {"fixtures": len(data["fixtures"]), "triples": data["triples"]}
            for pred, data in sorted(
                uncovered_counts[entity_name].items(),
                key=lambda x: (-len(x[1]["fixtures"]), -x[1]["triples"]),
            )
        }
        covered = {
            pred: {"fixtures": len(data["fixtures"]), "triples": data["triples"]}
            for pred, data in sorted(
                covered_counts[entity_name].items(),
                key=lambda x: (-len(x[1]["fixtures"]), -x[1]["triples"]),
            )
        }
        report["entities"][entity_name] = {
            "uncovered": uncovered,
            "covered": covered,
            "missing_frequency": {
                pred: count
                for pred, count in sorted(
                    missing_counts[entity_name].items(),
                    key=lambda x: -x[1],
                )
            },
        }

    elapsed = time.time() - t0
    print(f"Audit complete in {elapsed:.1f}s ({fixture_count} fixtures, {errors} errors)")

    return report


def generate_markdown(report: dict) -> str:
    """Convert an aggregated report dict into markdown."""
    template_name = report["template"]
    fixture_count = report["fixture_count"]
    errors = report["errors"]
    cov = report["coverage"]

    commit_hash = get_git_short_hash()
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = []

    lines.append("# Audit Coverage Report")
    lines.append("")
    lines.append(f"Generated from commit `{commit_hash}` on {timestamp}.")
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

    return "\n".join(lines)


def main() -> None:
    report = run_audit()
    markdown = generate_markdown(report)
    OUTPUT_PATH.write_text(markdown, encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
