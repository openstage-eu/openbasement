"""Subclass discovery, blank node handling, cycle detection, and owl:sameAs grouping."""

from rdflib import Graph, URIRef, BNode, RDF, RDFS, OWL


def find_instances(
    graph: Graph,
    type_uri: URIRef,
    include_subclasses: bool = False,
) -> list[URIRef | BNode]:
    """Find all instances of a given RDF type in the graph.

    Args:
        graph: The RDF graph to search.
        type_uri: The RDF type URI to find instances of.
        include_subclasses: If True, also find instances of direct subclasses
            (one level of rdfs:subClassOf).
    """
    types_to_check = {type_uri}

    if include_subclasses:
        for subclass in graph.subjects(RDFS.subClassOf, type_uri):
            types_to_check.add(subclass)

    instances: list[URIRef | BNode] = []
    seen: set[URIRef | BNode] = set()

    for t in types_to_check:
        for instance in graph.subjects(RDF.type, t):
            if instance not in seen:
                seen.add(instance)
                instances.append(instance)

    return instances


def extract_blank_node(
    graph: Graph,
    bnode: BNode,
    max_depth: int = 3,
    _depth: int = 0,
) -> dict:
    """Extract properties from a blank node into a dict.

    Recurses into nested blank nodes up to max_depth.
    """
    if _depth >= max_depth:
        return {"_blank": True, "_truncated": True}

    properties: dict[str, list] = {}
    for pred, obj in graph.predicate_objects(bnode):
        pred_str = str(pred)
        if pred_str not in properties:
            properties[pred_str] = []

        if isinstance(obj, BNode):
            properties[pred_str].append(
                extract_blank_node(graph, obj, max_depth, _depth + 1)
            )
        else:
            properties[pred_str].append(str(obj))

    return {"_blank": True, "properties": properties}


def follow_predicate(
    graph: Graph,
    subject: URIRef | BNode,
    predicate: URIRef,
    direction: str = "forward",
) -> list[URIRef | BNode]:
    """Follow a predicate from a subject, returning connected nodes.

    Args:
        graph: The RDF graph.
        subject: The starting node.
        predicate: The predicate to follow.
        direction: "forward" for (subject, pred, ?obj) or
                   "inverse" for (?subj, pred, subject).
    """
    if direction == "inverse":
        return list(graph.subjects(predicate, subject))
    return list(graph.objects(subject, predicate))


def _expand_same_as(graph: Graph, node: URIRef | BNode) -> set[URIRef | BNode]:
    """Find all nodes equivalent to this one via owl:sameAs (2-hop expansion).

    CDM data uses multiple URIs for the same entity (pegase IDs, procedure
    URIs, cellar UUIDs) linked by owl:sameAs through intermediate nodes.
    """
    aliases: set[URIRef | BNode] = {node}
    for obj in graph.objects(node, OWL.sameAs):
        aliases.add(obj)
    for subj in graph.subjects(OWL.sameAs, node):
        aliases.add(subj)
    # Second hop
    expanded = set(aliases)
    for alias in list(aliases):
        for obj in graph.objects(alias, OWL.sameAs):
            expanded.add(obj)
        for subj in graph.subjects(OWL.sameAs, alias):
            expanded.add(subj)
    return expanded


def group_same_as(
    instances: list[URIRef | BNode],
    graph: Graph,
) -> list[set[URIRef | BNode]]:
    """Group instances into equivalence classes via owl:sameAs.

    For each instance, expands sameAs 2 hops and merges overlapping groups.
    Returns a list of sets, one per unique logical entity.
    """
    # Map each instance to its equivalence class (index into groups list)
    node_to_group: dict[URIRef | BNode, int] = {}
    groups: list[set[URIRef | BNode]] = []

    for inst in instances:
        aliases = _expand_same_as(graph, inst)
        # Find all existing groups that overlap with this alias set
        existing_indices: set[int] = set()
        for alias in aliases:
            if alias in node_to_group:
                existing_indices.add(node_to_group[alias])

        if not existing_indices:
            # New group
            idx = len(groups)
            groups.append(aliases)
            for alias in aliases:
                node_to_group[alias] = idx
        else:
            # Merge into the lowest-numbered existing group
            target = min(existing_indices)
            merged = set(groups[target])
            merged.update(aliases)
            for other_idx in existing_indices:
                if other_idx != target:
                    merged.update(groups[other_idx])
                    groups[other_idx] = set()  # empty the merged group
            groups[target] = merged
            for alias in merged:
                node_to_group[alias] = target

    # Return non-empty groups
    return [g for g in groups if g]


def pick_canonical_uri(aliases: set[URIRef | BNode]) -> URIRef | BNode | None:
    """Pick the canonical URI from a set of sameAs aliases.

    Prefers resource/procedure/ URIs over pegase or cellar URIs.
    Falls back to shortest non-cellar, non-pegase URI, then any URIRef.
    """
    uri_refs = [a for a in aliases if isinstance(a, URIRef)]
    if not uri_refs:
        return None

    # Prefer procedure/ URIs
    for u in uri_refs:
        u_str = str(u)
        if "/procedure/" in u_str and "/cellar/" not in u_str and "/pegase/" not in u_str:
            return u

    # Fall back to shortest non-cellar, non-pegase URI
    non_internal = [
        u for u in uri_refs
        if "/cellar/" not in str(u) and "/pegase/" not in str(u)
    ]
    if non_internal:
        return min(non_internal, key=lambda u: len(str(u)))

    # Last resort: shortest URIRef
    return min(uri_refs, key=lambda u: len(str(u)))
