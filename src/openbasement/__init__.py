"""openbasement: Template-based RDF extraction from EU Cellar data.

Usage:
    from openbasement import extract, load_template, list_builtin_templates

    from rdflib import Graph
    graph = Graph()
    graph.parse("notice.rdf", format="xml")

    results = extract(graph, template="eu_procedure")
"""

from openbasement.template import load_template, list_builtin_templates
from openbasement.engine import extract_entities
from openbasement.audit import audit

from rdflib import Graph


def extract(
    graph: Graph,
    template: str | dict,
    entity: str | None = None,
    transforms: dict | None = None,
    merge_same_as: bool | None = None,
) -> list[dict]:
    """Extract structured data from an RDF graph using a template.

    Args:
        graph: An rdflib Graph loaded with RDF data.
        template: Template source -- built-in name (e.g. "eu_procedure"),
            path to YAML file, or a dict.
        entity: Optional entity name to extract. If None, extracts
            the first (root) entity defined in the template.
        transforms: Optional dict of custom transform name -> callable.
            These are merged with built-in transforms (custom takes
            precedence). Templates reference transforms by name via
            the "transform" field option.
        merge_same_as: If True, group owl:sameAs-equivalent instances
            and merge their triples into one entity. If False, extract
            each URI as a separate entity. If None (default), uses the
            template's same_as_merge setting (which defaults to True).

    Returns:
        List of extracted entity dicts.
    """
    normalized = load_template(template)

    return extract_entities(
        graph, normalized, entity_name=entity, transforms=transforms,
        merge_same_as=merge_same_as,
    )


__all__ = [
    "extract",
    "load_template",
    "list_builtin_templates",
    "audit",
]
