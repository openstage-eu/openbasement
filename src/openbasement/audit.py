"""Drift detection: compare template predicates against actual graph content."""

import logging
from typing import Any

from rdflib import Graph, URIRef, RDF

from openbasement.namespaces import build_namespace_map, resolve
from openbasement.matching import is_wildcard, matches_predicate
from openbasement.traversal import find_instances

logger = logging.getLogger(__name__)


def audit(graph: Graph, template: dict) -> dict[str, Any]:
    """Compare a template against actual predicates in a graph.

    For each entity type in the template, finds all instances and checks:
    - Which template predicates are missing from the graph
    - Which graph predicates are not covered by any template field or relation
    - Overall coverage percentage

    Args:
        graph: An rdflib Graph to audit.
        template: A normalized template dict (output of load_template).

    Returns:
        Dict with keys:
            "entities": per-entity audit results
            "summary": overall coverage stats
    """
    ns_map = build_namespace_map(template["prefixes"])
    entity_reports = {}

    total_covered = 0
    total_uncovered = 0

    for entity_name, entity_def in template["entities"].items():
        report = _audit_entity(graph, entity_def, ns_map)
        entity_reports[entity_name] = report
        total_covered += report["covered_triple_count"]
        total_uncovered += report["uncovered_triple_count"]

    total = total_covered + total_uncovered
    return {
        "entities": entity_reports,
        "summary": {
            "covered": total_covered,
            "uncovered": total_uncovered,
            "total": total,
            "coverage": total_covered / total if total > 0 else 0.0,
        },
    }


def _audit_entity(
    graph: Graph, entity_def: dict, ns_map: dict
) -> dict[str, Any]:
    """Audit a single entity type against the graph."""
    type_uri = resolve(entity_def["find"]["type"], ns_map)
    instances = find_instances(
        graph, type_uri, entity_def["find"]["include_subclasses"]
    )

    # Collect all template predicates (resolved URIs)
    template_exact: set[str] = set()
    template_wildcards: list[tuple[str, str]] = []  # (full_pattern, namespace_uri)

    for field_spec in entity_def["fields"].values():
        for pred in field_spec["predicate"]:
            if is_wildcard(pred):
                prefix, local = pred.split(":", 1)
                if prefix in ns_map:
                    ns_uri = str(ns_map[prefix])
                    template_wildcards.append((ns_uri + local, ns_uri))
            else:
                template_exact.add(str(resolve(pred, ns_map)))

    for rel_spec in entity_def["relations"].values():
        for pred in rel_spec["predicate"]:
            template_exact.add(str(resolve(pred, ns_map)))

    # Scan actual predicates across all instances
    graph_predicates: dict[str, int] = {}  # predicate URI -> count
    for instance in instances:
        for pred, _obj in graph.predicate_objects(instance):
            if pred == RDF.type:
                continue
            pred_str = str(pred)
            graph_predicates[pred_str] = graph_predicates.get(pred_str, 0) + 1

    # Classify each graph predicate
    covered: dict[str, int] = {}
    uncovered: dict[str, int] = {}

    for pred_str, count in graph_predicates.items():
        if pred_str in template_exact:
            covered[pred_str] = count
            continue

        matched = False
        for pattern, ns_uri in template_wildcards:
            if matches_predicate(URIRef(pred_str), pattern, ns_uri):
                covered[pred_str] = count
                matched = True
                break

        if not matched:
            uncovered[pred_str] = count

    # Check which template predicates are missing from graph
    missing = [p for p in template_exact if p not in graph_predicates]

    covered_count = sum(covered.values())
    uncovered_count = sum(uncovered.values())

    return {
        "instance_count": len(instances),
        "missing": sorted(missing),
        "uncovered": dict(sorted(uncovered.items(), key=lambda x: -x[1])),
        "covered": dict(sorted(covered.items(), key=lambda x: -x[1])),
        "covered_triple_count": covered_count,
        "uncovered_triple_count": uncovered_count,
    }
