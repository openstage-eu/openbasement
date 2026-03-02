"""Predicate frequency audit across all RDF fixtures.

Runs openbasement.audit() on every fixture and aggregates results into a
JSON report + stdout summary.  Standalone script, not a pytest test.

Usage:
    python tests/run_audit.py
"""

import json
import statistics
import sys
import time
from pathlib import Path

from rdflib import Graph

from openbasement import audit, load_template

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "procedures"
REPORT_PATH = Path(__file__).parent / "audit_report.json"
TEMPLATE_NAME = "eu_procedure"
TOP_N = 20


def main() -> None:
    template = load_template(TEMPLATE_NAME)
    rdf_files = sorted(FIXTURE_DIR.glob("*.rdf"))

    if not rdf_files:
        print(f"No .rdf files found in {FIXTURE_DIR}")
        sys.exit(1)

    print(f"Auditing {len(rdf_files)} fixtures with template '{TEMPLATE_NAME}'...")

    # Per-entity accumulators:
    #   uncovered_counts[entity][predicate] -> {fixtures: set, triples: int}
    #   covered_counts[entity][predicate]   -> {fixtures: set, triples: int}
    #   missing_counts[entity][predicate]   -> int (fixture count where missing)
    #   template_predicates[entity]         -> set of all template predicate URIs
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
            print(f"  ERROR parsing {rdf_file.name}: {e}")
            errors += 1
            continue

        coverages.append(result["summary"]["coverage"])
        fixture_id = rdf_file.stem

        for entity_name, entity_report in result["entities"].items():
            # Initialize accumulators on first encounter
            if entity_name not in uncovered_counts:
                uncovered_counts[entity_name] = {}
                covered_counts[entity_name] = {}
                missing_counts[entity_name] = {}
                template_predicates[entity_name] = set()

            # Uncovered predicates (not in template, found in graph)
            for pred, count in entity_report["uncovered"].items():
                if pred not in uncovered_counts[entity_name]:
                    uncovered_counts[entity_name][pred] = {"fixtures": set(), "triples": 0}
                uncovered_counts[entity_name][pred]["fixtures"].add(fixture_id)
                uncovered_counts[entity_name][pred]["triples"] += count

            # Covered predicates (in template and found in graph)
            for pred, count in entity_report["covered"].items():
                if pred not in covered_counts[entity_name]:
                    covered_counts[entity_name][pred] = {"fixtures": set(), "triples": 0}
                covered_counts[entity_name][pred]["fixtures"].add(fixture_id)
                covered_counts[entity_name][pred]["triples"] += count

            # Missing predicates (in template but not in graph)
            for pred in entity_report["missing"]:
                template_predicates[entity_name].add(pred)
                missing_counts[entity_name][pred] = missing_counts[entity_name].get(pred, 0) + 1

            # Also track template predicates that *were* found
            for pred in entity_report["covered"]:
                template_predicates[entity_name].add(pred)

    elapsed = time.time() - t0
    fixture_count = len(rdf_files) - errors

    # Build report
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
        # Convert sets to counts for JSON serialization, sort by fixture frequency
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

        # Missing = template predicates that never appeared in any fixture
        always_missing = sorted(
            pred
            for pred in template_predicates.get(entity_name, set())
            if missing_counts[entity_name].get(pred, 0) == fixture_count
        )

        report["entities"][entity_name] = {
            "uncovered": uncovered,
            "covered": covered,
            "missing_always": always_missing,
            "missing_frequency": {
                pred: count
                for pred, count in sorted(
                    missing_counts[entity_name].items(),
                    key=lambda x: -x[1],
                )
            },
        }

    # Write JSON report
    with open(REPORT_PATH, "w") as f:
        json.dump(report, f, indent=2)

    print(f"\nJSON report written to {REPORT_PATH}")

    # Stdout summary
    print(f"\n{'=' * 70}")
    print(f"AUDIT SUMMARY: {TEMPLATE_NAME}")
    print(f"{'=' * 70}")
    print(f"Fixtures processed: {fixture_count} ({errors} errors)")
    print(f"Runtime: {elapsed:.1f}s")
    print()

    cov = report["coverage"]
    print(f"Coverage (triple-level):")
    print(f"  mean={cov['mean']:.1%}  median={cov['median']:.1%}  "
          f"min={cov['min']:.1%}  max={cov['max']:.1%}  stdev={cov['stdev']:.4f}")
    print()

    for entity_name, entity_data in report["entities"].items():
        uncovered = entity_data["uncovered"]
        covered = entity_data["covered"]
        always_missing = entity_data["missing_always"]

        print(f"--- {entity_name} ---")
        print(f"  Covered predicates: {len(covered)}")
        print(f"  Uncovered predicates: {len(uncovered)}")
        print(f"  Template predicates never seen: {len(always_missing)}")

        if always_missing:
            print(f"  Dead aliases:")
            for pred in always_missing:
                print(f"    {pred}")

        if uncovered:
            print(f"\n  Top {TOP_N} uncovered predicates (by fixture frequency):")
            for pred, data in list(uncovered.items())[:TOP_N]:
                print(f"    {pred}")
                print(f"      fixtures: {data['fixtures']}/{fixture_count}  "
                      f"triples: {data['triples']}")

        print()


if __name__ == "__main__":
    main()
