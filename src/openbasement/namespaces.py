"""Namespace definitions and URI resolution for RDF extraction."""

from rdflib import Namespace, URIRef, RDF, RDFS, XSD
from rdflib.namespace import SKOS


CDM = Namespace("http://publications.europa.eu/ontology/cdm#")
AT = Namespace("http://publications.europa.eu/ontology/authority/")
CDM_CLASS = Namespace("http://publications.europa.eu/ontology/cdm/class#")

BUILTIN_PREFIXES: dict[str, Namespace | type] = {
    "cdm": CDM,
    "skos": SKOS,
    "rdf": RDF,
    "rdfs": RDFS,
    "xsd": XSD,
    "at": AT,
}


def build_namespace_map(
    prefix_definitions: dict[str, str],
) -> dict[str, Namespace]:
    """Build a namespace map from prefix string definitions.

    Merges user-defined prefixes with built-in defaults. User definitions
    override built-in ones.
    """
    ns_map: dict[str, Namespace] = {}
    for prefix, ns in BUILTIN_PREFIXES.items():
        if isinstance(ns, Namespace):
            ns_map[prefix] = ns
        else:
            # RDF, RDFS, XSD are module-level objects, get their base URI
            ns_map[prefix] = Namespace(str(ns))

    for prefix, uri in prefix_definitions.items():
        ns_map[prefix] = Namespace(uri)

    return ns_map


def resolve(prefixed: str, ns_map: dict[str, Namespace]) -> URIRef:
    """Resolve a prefixed URI like 'cdm:work_title' to a full URIRef.

    If the string is already a full URI (starts with http), returns it as URIRef.
    """
    if prefixed.startswith("http://") or prefixed.startswith("https://"):
        return URIRef(prefixed)

    if ":" not in prefixed:
        raise ValueError(f"Invalid prefixed URI: {prefixed!r} (no prefix separator)")

    prefix, local = prefixed.split(":", 1)
    if prefix not in ns_map:
        raise ValueError(
            f"Unknown prefix {prefix!r} in {prefixed!r}. "
            f"Known prefixes: {sorted(ns_map.keys())}"
        )

    return ns_map[prefix][local]
