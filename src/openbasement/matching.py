"""Predicate wildcard and pattern matching for RDF extraction."""

from fnmatch import fnmatch

from rdflib import URIRef


def is_wildcard(predicate_pattern: str) -> bool:
    """Check if a predicate pattern contains wildcards."""
    return "*" in predicate_pattern or "?" in predicate_pattern


def matches_predicate(
    predicate_uri: URIRef,
    pattern_uri: str,
    namespace_uri: str,
) -> bool:
    """Check if a predicate URI matches a wildcard pattern.

    The pattern is expressed as a local name pattern (e.g. 'date_*')
    and the namespace_uri provides the base namespace to strip.

    Args:
        predicate_uri: The actual predicate URI from the graph.
        pattern_uri: The full expanded URI pattern (with wildcards).
        namespace_uri: The namespace portion to strip for local name matching.
    """
    pred_str = str(predicate_uri)
    pattern_str = str(pattern_uri)

    # Direct fnmatch on full URIs
    return fnmatch(pred_str, pattern_str)


def extract_local_name(uri: URIRef | str, namespace_uri: str) -> str:
    """Extract the local name from a URI given its namespace.

    Falls back to the fragment or last path component if the namespace
    doesn't match.
    """
    uri_str = str(uri)

    if uri_str.startswith(namespace_uri):
        return uri_str[len(namespace_uri):]

    # Fallback: fragment
    if "#" in uri_str:
        return uri_str.rsplit("#", 1)[1]

    # Fallback: last path segment
    return uri_str.rsplit("/", 1)[-1]
