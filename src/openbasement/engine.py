"""Core extraction engine: template + graph -> structured dicts."""

import logging
from typing import Any

from rdflib import Graph, URIRef, Literal, BNode, RDF, OWL

from openbasement.namespaces import build_namespace_map, resolve
from openbasement.matching import is_wildcard, matches_predicate, extract_local_name
from openbasement.multilingual import resolve_language
from openbasement.traversal import (
    find_instances, extract_blank_node, follow_predicate,
    group_same_as, pick_canonical_uri,
)
from openbasement.transforms import apply_transform

logger = logging.getLogger(__name__)


def extract_entities(
    graph: Graph,
    template: dict,
    entity_name: str | None = None,
    transforms: dict | None = None,
    merge_same_as: bool | None = None,
) -> list[dict]:
    """Extract entities from an RDF graph using a normalized template.

    Args:
        graph: An rdflib Graph loaded with RDF data.
        template: A normalized template dict (output of load_template).
        entity_name: If provided, extract only this entity type.
            Otherwise extract the first (root) entity.
        transforms: Optional dict of custom transform name -> callable,
            merged with built-in transforms. Custom transforms take
            precedence over built-ins.
        merge_same_as: If True, group owl:sameAs-equivalent instances
            and merge their triples into one entity. If None, uses
            template["same_as_merge"] (defaults to True).

    Returns:
        List of extracted entity dicts.
    """
    ns_map = build_namespace_map(template["prefixes"])
    lang_config = template["languages"]

    # Resolve merge_same_as: function parameter overrides template setting
    if merge_same_as is None:
        merge_same_as = template.get("same_as_merge", True)

    if entity_name:
        if entity_name not in template["entities"]:
            raise ValueError(
                f"Entity {entity_name!r} not found in template. "
                f"Available: {sorted(template['entities'].keys())}"
            )
        entity_def = template["entities"][entity_name]
    else:
        # Use first entity as root
        entity_name = next(iter(template["entities"]))
        entity_def = template["entities"][entity_name]

    # Find instances
    type_uri = resolve(entity_def["find"]["type"], ns_map)
    instances = find_instances(
        graph, type_uri, entity_def["find"]["include_subclasses"]
    )

    if merge_same_as:
        # Group by owl:sameAs equivalence and merge
        groups = group_same_as(instances, graph)

        results = []
        for alias_set in groups:
            canonical = pick_canonical_uri(alias_set)
            result = _extract_single_entity(
                graph, canonical or next(iter(alias_set)), entity_def,
                template, ns_map, lang_config,
                visited=set(), transforms=transforms,
                aliases=alias_set,
            )
            results.append(result)
    else:
        # No merging: extract each instance independently
        results = []
        for instance in instances:
            result = _extract_single_entity(
                graph, instance, entity_def, template, ns_map, lang_config,
                visited=set(), transforms=transforms,
            )
            results.append(result)

    return results


def _extract_single_entity(
    graph: Graph,
    instance: URIRef | BNode,
    entity_def: dict,
    template: dict,
    ns_map: dict,
    lang_config: dict,
    visited: set,
    transforms: dict | None = None,
    aliases: set | None = None,
) -> dict:
    """Extract a single entity instance into a flat dict.

    Output shape: fields and relations are top-level keys.
    Metadata keys are prefixed with underscore: _uri, _rdf_types, _raw_triples,
    _same_as.

    When aliases is provided, queries all alias subjects (the owl:sameAs
    equivalence class) and merges their data into a single entity.
    """
    subjects = aliases if aliases else {instance}

    instance_key = str(instance)
    if instance_key in visited:
        return {"_uri": instance_key, "_cycle": True}
    visited = visited | {instance_key}
    # Also mark all aliases as visited to prevent re-extraction
    for alias in subjects:
        visited = visited | {str(alias)}

    # Get RDF types from all aliases
    rdf_types_set: set[str] = set()
    for subj in subjects:
        for t in graph.objects(subj, RDF.type):
            rdf_types_set.add(str(t))

    # Start with metadata
    result: dict[str, Any] = {
        "_uri": str(instance) if isinstance(instance, URIRef) else None,
        "_rdf_types": sorted(rdf_types_set),
    }

    # Add _same_as if there are multiple aliases
    if aliases and len(aliases) > 1:
        result["_same_as"] = sorted(str(a) for a in aliases if str(a) != str(instance))

    # Extract fields (flat, directly into result)
    consumed_predicates: set[URIRef] = set()

    for field_name, field_spec in entity_def["fields"].items():
        field_value, used_preds = _extract_field(
            graph, instance, field_spec, ns_map, lang_config, transforms,
            aliases=subjects,
        )
        result[field_name] = field_value
        consumed_predicates.update(used_preds)

    # Extract relations (flat, directly into result)
    for rel_name, rel_spec in entity_def["relations"].items():
        rel_value, used_preds = _extract_relation(
            graph, instance, rel_spec, template, ns_map, lang_config,
            visited, transforms, aliases=subjects,
        )
        result[rel_name] = rel_value
        consumed_predicates.update(used_preds)

    # Collect raw (unconsumed) triples from all aliases
    raw_triples = []
    for subj in subjects:
        for pred, obj in graph.predicate_objects(subj):
            if pred not in consumed_predicates and pred != RDF.type and pred != OWL.sameAs:
                raw_triples.append((str(subj), str(pred), str(obj)))

    result["_raw_triples"] = raw_triples

    return result


def _extract_field(
    graph: Graph,
    instance: URIRef | BNode,
    field_spec: dict,
    ns_map: dict,
    lang_config: dict,
    transforms: dict | None = None,
    aliases: set | None = None,
) -> tuple[Any, set[URIRef]]:
    """Extract a single field value from the graph.

    Supports predicate aliasing: field_spec["predicate"] is a list of
    alternative predicates. For exact predicates, each is tried and results
    are merged. For wildcards, each pattern is matched independently.

    When aliases is provided, queries all alias subjects and merges results.

    Returns (field_value, set_of_consumed_predicate_URIs).
    """
    subjects = aliases if aliases else {instance}
    predicates = field_spec["predicate"]  # always a list after normalization
    direction = field_spec.get("direction", "forward")
    cardinality = field_spec.get("cardinality", "one")
    multilingual = field_spec.get("multilingual", False)
    datatype = field_spec.get("datatype")
    follow_spec = field_spec.get("follow")
    transform_name = field_spec.get("transform")
    consumed: set[URIRef] = set()

    # Separate wildcard and exact predicates
    wildcard_preds = [p for p in predicates if is_wildcard(p)]
    exact_preds = [p for p in predicates if not is_wildcard(p)]

    # Handle wildcard predicates (merge results from all patterns)
    if wildcard_preds:
        merged_result: dict[str, Any] = {}
        for pattern in wildcard_preds:
            for subj in subjects:
                partial, partial_consumed = _extract_wildcard_field(
                    graph, subj, pattern, field_spec,
                    ns_map, lang_config, set()
                )
                consumed.update(partial_consumed)
                if isinstance(partial, dict):
                    for key, val in partial.items():
                        if key not in merged_result:
                            merged_result[key] = val
        if transform_name:
            merged_result = {
                k: apply_transform(v, transform_name, transforms)
                for k, v in merged_result.items()
            }
        return merged_result, consumed

    # Handle exact predicates (alias list)
    all_objects: list = []
    found = False
    for pred_pattern in exact_preds:
        pred_uri = resolve(pred_pattern, ns_map)
        consumed.add(pred_uri)

        for subj in subjects:
            if direction == "inverse":
                objects = list(graph.subjects(pred_uri, subj))
            else:
                objects = list(graph.objects(subj, pred_uri))

            if objects:
                all_objects.extend(objects)
                found = True

        if found and cardinality == "one":
            break

    if not all_objects:
        if field_spec.get("required"):
            logger.warning(
                "Required field missing: predicates=%s instance=%s",
                predicates, instance
            )
        empty = [] if cardinality == "many" else None
        return empty, consumed

    # Follow one-hop if specified
    if follow_spec:
        all_objects = _follow_objects(graph, all_objects, follow_spec, ns_map)
        if follow_spec.get("multilingual", False):
            multilingual = True

    # Handle multilingual
    if multilingual:
        literals = [o for o in all_objects if isinstance(o, Literal)]
        lang_result = resolve_language(
            literals, lang_config["preferred"], lang_config["fallback"]
        )
        if transform_name and lang_result:
            lang_result = {
                k: apply_transform(v, transform_name, transforms)
                for k, v in lang_result.items()
            }
        return lang_result, consumed

    # Handle datatype
    if datatype:
        values = [_format_typed_value(o, datatype) for o in all_objects]
    else:
        values = [_format_value(o, graph) for o in all_objects]

    # Apply transform
    if transform_name:
        values = [apply_transform(v, transform_name, transforms) for v in values]

    if cardinality == "many":
        values = list(dict.fromkeys(values))
        return values, consumed

    return values[0] if values else None, consumed


def _extract_wildcard_field(
    graph: Graph,
    instance: URIRef | BNode,
    predicate_pattern: str,
    field_spec: dict,
    ns_map: dict,
    lang_config: dict,
    consumed: set[URIRef],
) -> tuple[Any, set[URIRef]]:
    """Extract wildcard-matched fields into a dict."""
    collect_mode = field_spec.get("collect", "dict")
    exclude = field_spec.get("exclude", [])
    multilingual = field_spec.get("multilingual", False)

    # Resolve the pattern to a full URI pattern
    if ":" in predicate_pattern and not predicate_pattern.startswith("http"):
        prefix, local = predicate_pattern.split(":", 1)
        if prefix in ns_map:
            namespace_uri = str(ns_map[prefix])
            full_pattern = namespace_uri + local
        else:
            full_pattern = predicate_pattern
            namespace_uri = ""
    else:
        full_pattern = predicate_pattern
        namespace_uri = ""

    # Resolve exclusions
    exclude_uris = set()
    for exc in exclude:
        try:
            exclude_uris.add(str(resolve(exc, ns_map)))
        except ValueError:
            exclude_uris.add(exc)

    result: dict[str, Any] = {}

    for pred, obj in graph.predicate_objects(instance):
        pred_str = str(pred)

        if pred_str in exclude_uris:
            continue

        if matches_predicate(pred, full_pattern, namespace_uri):
            consumed.add(pred)
            local_name = extract_local_name(pred, namespace_uri)

            if multilingual and isinstance(obj, Literal) and obj.language:
                # Collect multilingual values per predicate
                if local_name not in result:
                    result[local_name] = []
                result[local_name].append(obj)
            else:
                value = _format_value(obj, graph)
                # For dict collect, keep first value per local name
                if local_name not in result:
                    result[local_name] = value

    # If multilingual, resolve each collected predicate
    if multilingual:
        for key, literals in result.items():
            if isinstance(literals, list):
                result[key] = resolve_language(
                    literals, lang_config["preferred"], lang_config["fallback"]
                )

    return result, consumed


def _extract_relation(
    graph: Graph,
    instance: URIRef | BNode,
    rel_spec: dict,
    template: dict,
    ns_map: dict,
    lang_config: dict,
    visited: set,
    transforms: dict | None = None,
    aliases: set | None = None,
) -> tuple[Any, set[URIRef]]:
    """Extract a relation (nested entities) from the graph.

    Supports predicate aliasing: rel_spec["predicate"] is a list of
    alternative predicates. All aliases are tried, related nodes are merged.

    When aliases is provided, queries all alias subjects for forward lookups
    and uses them directly for inverse predicate lookups.
    """
    subjects = aliases if aliases else {instance}
    predicates = rel_spec["predicate"]  # always a list after normalization
    consumed: set[URIRef] = set()
    direction = rel_spec.get("direction", "forward")
    cardinality = rel_spec.get("cardinality", "many")
    target_template_name = rel_spec.get("target_template")
    inverse_predicates = rel_spec.get("inverse_predicate", [])
    if isinstance(inverse_predicates, str):
        inverse_predicates = [inverse_predicates]

    seen: set = set()
    related_nodes: list = []

    def _add_unique(nodes: list) -> None:
        for n in nodes:
            key = str(n)
            if key not in seen:
                seen.add(key)
                related_nodes.append(n)

    # Forward (or explicit direction) lookup across all aliases
    for pred_pattern in predicates:
        pred_uri = resolve(pred_pattern, ns_map)
        consumed.add(pred_uri)

        found = False
        for subj in subjects:
            if direction == "inverse":
                nodes = list(graph.subjects(pred_uri, subj))
            else:
                nodes = list(graph.objects(subj, pred_uri))

            if nodes:
                _add_unique(nodes)
                found = True

        if found and cardinality == "one":
            break

    # Inverse predicate lookup: find nodes that point TO any alias.
    # When aliases are already provided (from sameAs grouping), use them
    # directly. Otherwise fall back to sameAs expansion for this instance.
    if inverse_predicates:
        inv_aliases = subjects if aliases else _find_same_as_aliases(graph, instance)
        for pred_pattern in inverse_predicates:
            pred_uri = resolve(pred_pattern, ns_map)
            consumed.add(pred_uri)
            for alias in inv_aliases:
                nodes = list(graph.subjects(pred_uri, alias))
                _add_unique(nodes)

    if not related_nodes:
        return [] if cardinality == "many" else None, consumed

    # Get target entity definition
    if target_template_name and target_template_name in template["entities"]:
        target_def = template["entities"][target_template_name]
    else:
        # No target template -- just return URIs
        values = [str(n) for n in related_nodes]
        if cardinality == "many":
            return values, consumed
        return values[0] if values else None, consumed

    # Recursively extract related entities
    results = []
    for node in related_nodes:
        if isinstance(node, (URIRef, BNode)):
            extracted = _extract_single_entity(
                graph, node, target_def, template, ns_map, lang_config,
                visited, transforms,
            )
            results.append(extracted)
        else:
            results.append(str(node))

    if cardinality == "many":
        return results, consumed
    return results[0] if results else None, consumed


def _follow_objects(
    graph: Graph,
    objects: list,
    follow_spec: dict,
    ns_map: dict,
) -> list:
    """One-hop traversal: for each object, follow the specified predicate."""
    follow_pred = resolve(follow_spec["predicate"], ns_map)
    followed = []
    for obj in objects:
        if isinstance(obj, (URIRef, BNode)):
            for target in graph.objects(obj, follow_pred):
                followed.append(target)
    return followed if followed else objects


def _find_same_as_aliases(graph: Graph, node: URIRef | BNode) -> set:
    """Find all nodes equivalent to this one via owl:sameAs chains.

    CDM data uses multiple URIs for the same entity (pegase IDs, procedure
    URIs, cellar UUIDs) linked by owl:sameAs through intermediate nodes.
    This follows sameAs one hop in each direction to collect all aliases.
    """
    aliases = {node}
    # Direct sameAs from/to this node
    for obj in graph.objects(node, OWL.sameAs):
        aliases.add(obj)
    for subj in graph.subjects(OWL.sameAs, node):
        aliases.add(subj)
    # One more hop: for each alias found, check their sameAs too
    expanded = set(aliases)
    for alias in list(aliases):
        for obj in graph.objects(alias, OWL.sameAs):
            expanded.add(obj)
        for subj in graph.subjects(OWL.sameAs, alias):
            expanded.add(subj)
    return expanded


def _format_value(obj: Any, graph: Graph) -> Any:
    """Format an RDF object for output."""
    if isinstance(obj, Literal):
        return str(obj)
    if isinstance(obj, URIRef):
        return str(obj)
    if isinstance(obj, BNode):
        return extract_blank_node(graph, obj)
    return str(obj)


def _format_typed_value(obj: Any, datatype: str) -> Any:
    """Format a typed literal value."""
    val = str(obj)
    if "date" in datatype.lower():
        return val
    if "integer" in datatype.lower() or "int" in datatype.lower():
        try:
            return int(val)
        except ValueError:
            return val
    if "float" in datatype.lower() or "double" in datatype.lower() or "decimal" in datatype.lower():
        try:
            return float(val)
        except ValueError:
            return val
    if "boolean" in datatype.lower():
        return val.lower() in ("true", "1")
    return val
